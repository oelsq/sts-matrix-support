import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from nio import RoomMessageText, RoomSendResponse
from conversation import ConversationMemory
from matrix_inbox import InboxStore
from matrix_delivery import RequestDispatcher, sync_batch


def event(ident='$q',body='Что делать?',ts=100_001):
    return RoomMessageText.from_dict({'type':'m.room.message','event_id':ident,'sender':'@user:test','origin_server_ts':ts,'content':{'msgtype':'m.text','body':body}})

def store(tmp_path,clock=None):return InboxStore(tmp_path/'inbox.sqlite3','synthetic',clock=clock or (lambda:100))

def enqueue(s,e):
    s.commit_sync('cursor',[( '!ai:test',e)],{'!ai:test':e.event_id})

class Matrix:
    rooms={}
    def __init__(self):self.events={};self.calls=[];self.crash=False
    async def room_send(self,**kwargs):
        self.calls.append(kwargs)
        key=kwargs['tx_id']
        self.events.setdefault(key,dict(kwargs['content']))
        if self.crash:raise asyncio.CancelledError()
        return RoomSendResponse('$reply'+key,'!ai:test')


def test_first_install_cutoff_survives_restart_and_history_is_not_replayed(tmp_path):
    s=store(tmp_path);enqueue(s,event('$old',ts=99_000));assert s.pending()==0;s.close()
    s=store(tmp_path,lambda:200);enqueue(s,event('$offline',ts=150_000));assert s.pending()==1
    assert s.claim()['event_id']=='$offline';s.close()


def test_cursor_and_events_commit_atomically_and_duplicate_event_is_not_requeued(tmp_path):
    s=store(tmp_path);enqueue(s,event())
    with pytest.raises(AttributeError):s.commit_sync('new',[( '!ai:test',event('$next')),('!ai:test',object())],{})
    assert s.cursor=='cursor' and s.pending()==1
    enqueue(s,event());assert s.pending()==1
    s.finish('$q');enqueue(s,event());assert s.pending()==0;s.close()


def test_only_one_process_can_recover_processing_requests_and_identity_is_bound(tmp_path):
    s=store(tmp_path);enqueue(s,event());s.claim()
    with pytest.raises(RuntimeError,match='Another bot'):store(tmp_path)
    s.close()
    with pytest.raises(RuntimeError,match='another Matrix'):InboxStore(tmp_path/'inbox.sqlite3','other',clock=lambda:100)
    s=store(tmp_path);assert s.claim()['state']=='pending';s.close()
    assert (tmp_path/'inbox.sqlite3').stat().st_mode&0o777==0o600


def test_ambiguous_send_reuses_identical_payload_and_transaction_after_restart(tmp_path):
    async def run():
        s=store(tmp_path);enqueue(s,event());matrix=Matrix();calls=[]
        async def handler(room,e):
            calls.append(e.body)
            await dispatcher.send(room.room_id,'m.room.message',{'msgtype':'m.text','body':'Проверенный ответ'})
        dispatcher=RequestDispatcher(s,matrix,handler);matrix.crash=True
        with pytest.raises(asyncio.CancelledError):await dispatcher.process_next()
        assert s.final_ready('$q');s.close()
        s=store(tmp_path);matrix.crash=False
        dispatcher=RequestDispatcher(s,matrix,handler)
        assert await dispatcher.process_next()
        assert calls==['Что делать?'] and len(matrix.events)==1
        assert matrix.calls[0]==matrix.calls[1]
        assert s.pending()==0
        assert s.db.execute('SELECT body FROM inbox_requests').fetchone()[0]==''
        assert s.db.execute('SELECT content FROM inbox_outbox').fetchone()[0]==''
        assert not await dispatcher.process_next();s.close()
    asyncio.run(run())


def test_failed_send_remains_pending_and_does_not_regenerate_answer(tmp_path):
    async def run():
        now=[100];s=store(tmp_path,lambda:now[0]);enqueue(s,event());matrix=Matrix();calls=[]
        good=matrix.room_send
        async def failed(**kwargs):return NS(status_code='M_LIMIT_EXCEEDED')
        matrix.room_send=failed
        async def handler(room,e):
            calls.append(e.event_id)
            await dispatcher.send(room.room_id,'m.room.message',{'msgtype':'m.text','body':'Ответ'})
        dispatcher=RequestDispatcher(s,matrix,handler)
        assert await dispatcher.process_next() and s.pending()==1
        assert not await dispatcher.process_next()
        now[0]+=60;matrix.room_send=good
        assert await dispatcher.process_next() and s.pending()==0 and calls==['$q'];s.close()
    asyncio.run(run())


def test_question_context_is_identical_after_crash_before_generation_finishes(tmp_path):
    s=store(tmp_path)
    c=ConversationMemory(persistent_store=s)
    c.resolve('!ai:test','@user:test','legal','Начальник не отдаёт деньги за работу',request_id='$one')
    expected=c.resolve('!ai:test','@user:test','legal','А как написать требование?',request_id='$two')
    assert expected[1];s.close()
    s=store(tmp_path);c=ConversationMemory(persistent_store=s)
    assert c.resolve('!ai:test','@user:test','legal','А как написать требование?',request_id='$two')==expected
    history=s.load_history('!ai:test','@user:test','legal',1800)
    assert history.count('А как написать требование?')==1
    assert c.resolve('!ai:test','@another:test','legal','А как написать требование?')[1]==''
    c.clear('!ai:test','@user:test')
    assert s.load_history('!ai:test','@user:test','legal',1800)==[]
    s.finish('$two');assert s.prepared_context('$two','legal') is None;s.close()


def response(events,*,limited=True,prev='p0',cursor='new',human=False):
    joined={'!ai:test':NS(timeline=NS(events=events,limited=limited,prev_batch=prev))}
    if human:joined['!human:test']=NS(timeline=NS(events=[event('$human')],limited=True,prev_batch='human'))
    return NS(next_batch=cursor,rooms=NS(join=joined))


def test_limited_sync_recovers_all_missing_questions_in_order_and_skips_human_rooms(tmp_path):
    async def run():
        s=store(tmp_path);s.commit_sync('old',[],{'!ai:test':'$anchor'})
        calls=[]
        class Client:
            async def room_messages(self,room,*,start,limit):
                calls.append((room,start))
                if start=='p0':return NS(chunk=[event('$gap2'),event('$gap1')],end='p1')
                return NS(chunk=[event('$anchor')],end='p2')
        await sync_batch(Client(),response([event('$tail')],human=True),s,lambda room:room!='!human:test',lambda *_:True)
        assert calls==[('!ai:test','p0'),('!ai:test','p1')]
        assert [r[0] for r in s.db.execute('SELECT event_id FROM inbox_requests ORDER BY seq')]==['$gap1','$gap2','$tail']
        assert s.cursor=='new' and s.anchor('!ai:test')=='$tail';s.close()
    asyncio.run(run())

@pytest.mark.parametrize('failure',['missing_cursor','missing_anchor','failed_history','page_cap','unchanged_cursor'])
def test_unrecoverable_sync_gap_does_not_advance_cursor_or_lose_pending_questions(tmp_path,failure):
    async def run():
        s=store(tmp_path);s.commit_sync('old',[],{'!ai:test':'$anchor'})
        class Client:
            async def room_messages(self,*args,**kwargs):
                if failure=='failed_history':return NS(status_code='M_FORBIDDEN')
                if failure=='missing_anchor':return NS(chunk=[],end=None)
                return NS(chunk=[event('$gap')],end='p0' if failure=='unchanged_cursor' else 'p1')
        with pytest.raises(RuntimeError):await sync_batch(Client(),response([event('$tail')],prev=None if failure=='missing_cursor' else 'p0'),s,lambda _:True,lambda *_:True,page_limit=1)
        assert s.cursor=='old' and s.anchor('!ai:test')=='$anchor' and s.pending()==0;s.close()
    asyncio.run(run())


def test_anchor_in_limited_tail_does_not_require_older_history(tmp_path):
    async def run():
        s=store(tmp_path);enqueue(s,event('$anchor'));s.finish('$anchor')
        await sync_batch(object(),response([event('$anchor'),event('$tail')],prev=None),s,lambda _:True,lambda *_:True)
        assert s.pending()==1 and s.claim()['event_id']=='$tail';s.close()
    asyncio.run(run())


def test_real_sync_callback_path_restores_session_and_uses_persisted_cursor(monkeypatch,tmp_path):
    import bot
    clients=[];sent=[];login_count=[];restore_count=[]
    class Login:
        user_id='@bot:test';device_id='DEVICE';access_token='synthetic-secret'
    class Matrix:
        user='@bot:test';rooms={}
        def __init__(self,*args,**kwargs):self.index=len(clients);clients.append(self)
        def add_event_callback(self,*args):pass
        def add_response_callback(self,callback,*args):self.checkpoint=callback
        def restore_login(self,*args):restore_count.append(args)
        async def whoami(self):return NS(user_id=self.user)
        async def login(self,*args,**kwargs):login_count.append(1);return Login()
        async def close(self):pass
        async def room_send(self,**kwargs):sent.append(kwargs);return RoomSendResponse('$sent','!ai:test')
        async def sync_forever(self,**kwargs):
            assert kwargs['since']==(None if self.index==0 else 'sync0')
            event_list=[event('$help','!помощь',int(__import__('time').time()*1000)+1000)]
            await self.checkpoint(response(event_list,limited=False,cursor='sync'+str(self.index)))
            for _ in range(200):
                if sent:break
                await asyncio.sleep(.001)
            assert sent
    settings={'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:test','MATRIX_PASSWORD':'synthetic','LLM_ENDPOINT':'http://litellm:4000/v1','LLM_MODEL':'synthetic','OFFLINE_MODE':'0','KNOWLEDGE_DIR':'','MATRIX_INBOX_DB':str(tmp_path/'inbox.sqlite3'),'MATRIX_ROOM_ROUTES_FILE':str(tmp_path/'absent')}
    for key,value in settings.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(bot,'AsyncClient',Matrix);monkeypatch.setattr(bot,'LoginResponse',Login)
    monkeypatch.setattr(bot,'read_llm_key',lambda:'synthetic');monkeypatch.setattr(bot,'department_roster',lambda _: {})
    from flowise_client import FlowiseClient
    monkeypatch.setattr(FlowiseClient,'from_env',lambda _:None)
    asyncio.run(bot.main());asyncio.run(bot.main())
    assert len(login_count)==1 and len(restore_count)==1 and len(sent)==1
    assert sent[0]['content']['org.sts.request_id']=='$help'
    assert sent[0]['tx_id']==sent[0]['content']['org.sts.reply_key']


def test_new_room_history_before_bot_join_is_not_answered(tmp_path):
    from nio import RoomMemberEvent
    async def run():
        s=store(tmp_path)
        reply=response([event('$old_room_history',ts=150_000),event('$after_join',ts=200_001)],limited=False)
        join=RoomMemberEvent.from_dict({'type':'m.room.member','event_id':'$join','sender':'@bot:test','state_key':'@bot:test','origin_server_ts':200_000,'content':{'membership':'join'}})
        reply.rooms.join['!ai:test'].state=NS(events=[join])
        await sync_batch(NS(user='@bot:test'),reply,s,lambda _:True,lambda *_:True)
        assert s.pending()==1 and s.claim()['event_id']=='$after_join';s.close()
    asyncio.run(run())
