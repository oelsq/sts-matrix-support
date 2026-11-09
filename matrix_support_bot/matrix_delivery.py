"""Persist before acknowledging sync; send frozen replies with stable transactions."""
import asyncio
import json
import logging
from contextvars import ContextVar
from types import SimpleNamespace

from nio import MegolmEvent, RoomMessageText, RoomSendResponse, RoomMemberEvent
from matrix_transport import DeliveryDeferred

log = logging.getLogger('support_bot.inbox')


class RequestDispatcher:
    def __init__(self, store, client, handler, *, allow_unconfirmed=False, sender=None):
        self.store, self.client, self.handler = store, client, handler
        self.allow_unconfirmed = allow_unconfirmed
        self.sender=sender
        self._request = ContextVar("matrix_request", default=None)
        self.wake = asyncio.Event()

    @property
    def current(self):
        return self._request.get()

    @current.setter
    def current(self, value):
        self._request.set(value)

    async def send(self, room_id, message_type, content, *, kind='final', final=True):
        if self.current is None or room_id != self.current['room_id'] or message_type!='m.room.message':
            raise RuntimeError('Reply is outside its request scope')
        ident=self.current['event_id']
        frozen=dict(content, **{'org.sts.request_id':ident,'org.sts.reply_key':self.store.transaction_id(ident,kind)})
        row=self.store.prepare_reply(ident,kind,frozen,final)
        return await self._send_row(row)

    async def _send_row(self, row):
        if row['sent_id']:
            return RoomSendResponse(row['sent_id'],self.current['room_id'])
        kwargs=dict(room_id=self.current['room_id'],message_type='m.room.message',content=json.loads(row['content']))
        # Matrix transaction deduplication survives restarts because login is restored.
        if not self.allow_unconfirmed:kwargs['tx_id']=self.store.transaction_id(row['event_id'],row['kind'])
        self.store.delivery_attempt(row['event_id'])
        try:
            result=await (self.sender or self.client.room_send)(**kwargs)
        except DeliveryDeferred as exc:
            self.store.delivery_deferred(row['event_id'],server_rate_limit=exc.server_rate_limit)
            raise
        sent_id=getattr(result,'event_id',None)
        if not sent_id and self.allow_unconfirmed:sent_id='$unconfirmed-test-client'
        if not isinstance(sent_id,str) or not sent_id:
            raise RuntimeError('Matrix did not confirm reply delivery')
        self.store.sent(row['event_id'],row['kind'],sent_id)
        return result

    async def process_next(self):
        # A homeserver cooldown applies to all sends of this account. Do not
        # repeatedly claim already frozen replies while it lasts. Independent
        # users may still prepare replies; same-user order remains in SQL.
        blocked = bool(self.sender and getattr(self.sender, 'cooldown_remaining', 0) > 0)
        row=self.store.claim(delivery_blocked=blocked)
        if row is None:return False
        self.current=row
        try:
            if self.store.final_ready(row['event_id']):
                # A crash after generation must not generate a different answer.
                for reply in self.store.replies(row['event_id']):await self._send_row(reply)
            else:
                event=SimpleNamespace(event_id=row['event_id'],sender=row['sender'],body=row['body'],server_timestamp=row['timestamp'])
                room=self.client.rooms.get(row['room_id']) if hasattr(self.client,'rooms') else None
                await self.handler(room or SimpleNamespace(room_id=row['room_id']),event)
            self.store.finish(row['event_id'])
        except asyncio.CancelledError:
            # Processing is returned to pending by the next exclusively owning process.
            raise
        except DeliveryDeferred as exc:
            self.store.retry(row['event_id'],row['attempts']+1,delay=exc.seconds)
            log.info('Frozen reply retained until Matrix cooldown expires')
        except Exception as exc:
            self.store.retry(row['event_id'],row['attempts']+1)
            log.error('Request retained for retry (%s)',type(exc).__name__)
        finally:
            self.current=None
        return True

    async def run(self, ready, workers=1):
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ValueError('Matrix workers must be between 1 and 4')
        await ready.wait()
        async with asyncio.TaskGroup() as group:
            for _ in range(workers):
                group.create_task(self._worker())

    async def _worker(self):
        while True:
            self.wake.clear()
            while await self.process_next():pass
            try:await asyncio.wait_for(self.wake.wait(),timeout=1)
            except asyncio.TimeoutError:pass


def event_id(event):
    return getattr(event,'event_id',None) or getattr(event,'source',{}).get('event_id')


async def sync_batch(client, response, store, eligible_room, eligible_event, *, page_limit=500, crypto=None):
    """Recover a limited timeline before atomically saving requests and next_batch.

    A missing anchor or a failed history request stops checkpoint advancement.
    Human-department rooms are excluded before any history request is made.
    """
    events,anchors=[],{}
    secure_events={}
    for room_id,joined in response.rooms.join.items():
        if not eligible_room(room_id):continue
        timeline=joined.timeline
        tail=list(timeline.events)
        recovered=[]
        anchor=store.anchor(room_id)
        tail_ids={event_id(e) for e in tail}
        needs_recovery=timeline.limited and (not anchor or anchor not in tail_ids)
        if needs_recovery and not timeline.prev_batch:
            raise RuntimeError('Limited Matrix timeline has no recovery cursor')
        if needs_recovery:
            token=timeline.prev_batch
            boundary=False
            for _ in range(page_limit):
                page=await client.room_messages(room_id,start=token,limit=100)
                if not hasattr(page,'chunk'):
                    raise RuntimeError('Matrix history recovery failed')
                chunk=list(page.chunk)
                for event in chunk:
                    if anchor and event_id(event)==anchor:
                        boundary=True;break
                    recovered.append(event)
                if boundary:break
                if not chunk:
                    # Without an anchor this is the initial room bootstrap.
                    if anchor:raise RuntimeError('Matrix recovery anchor is unavailable')
                    boundary=True;break
                if not anchor and all(getattr(e,'server_timestamp',store.bootstrap_ms)<store.bootstrap_ms for e in chunk):
                    boundary=True;break
                following=getattr(page,'end',None)
                if not following:
                    if anchor:raise RuntimeError('Matrix recovery ended before saved anchor')
                    boundary=True;break
                if following==token:raise RuntimeError('Matrix history cursor did not advance')
                token=following
            if not boundary:raise RuntimeError('Matrix recovery page limit reached; sync not acknowledged')
            recovered.reverse()
        state_events=getattr(getattr(joined,'state',None),'events',[]) or []
        own_joins=[e.server_timestamp for e in list(state_events)+recovered+tail
                   if isinstance(e,RoomMemberEvent) and e.state_key==getattr(client,'user',None)
                   and e.membership=='join']
        cutoff=max([store.bootstrap_ms]+own_joins)
        for event in recovered+tail:
            if isinstance(event,(RoomMessageText,MegolmEvent)) and event.server_timestamp>=cutoff and eligible_event(room_id,event):
                if isinstance(event,MegolmEvent) and crypto is None:
                    raise RuntimeError('Encrypted room requires configured E2EE; cursor not advanced')
                protected=crypto.classify(room_id,event) if crypto else None
                if protected is False:continue
                if protected is not None:secure_events[event.event_id]=protected
                events.append((room_id,event))
        if tail and event_id(tail[-1]):anchors[room_id]=event_id(tail[-1])
    if crypto:store.commit_sync(response.next_batch,events,anchors,secure_events=secure_events)
    else:store.commit_sync(response.next_batch,events,anchors)
