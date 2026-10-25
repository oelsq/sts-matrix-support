"""Bounded Matrix delivery with shared server cooldown; typing never blocks answers."""
import asyncio,math,time
from urllib.parse import quote
from nio import RoomSendResponse

class DeliveryDeferred(RuntimeError):
    def __init__(self,seconds,*,server_rate_limit=False):
        self.seconds=max(.05,float(seconds));self.server_rate_limit=server_rate_limit
        super().__init__('Matrix delivery deferred by server cooldown')


class MatrixSender:
    def __init__(self,http,client,origin,*,clock=time.monotonic):
        self.http,self.client,self.origin=http,client,origin.rstrip('/');self.clock=clock;self.not_before=0.;self.lock=asyncio.Lock()
        self.metrics={'sent':0,'rate_limited':0,'cooldown_deferred':0}

    @property
    def cooldown_remaining(self):
        return max(0., self.not_before-self.clock())

    async def __call__(self,*,room_id,message_type,content,tx_id):
        async with self.lock:
            wait=self.not_before-self.clock()
            if wait>0:self.metrics['cooldown_deferred']+=1;raise DeliveryDeferred(wait)
            token=getattr(self.client,'access_token',None)
            if not token:raise RuntimeError('Matrix session is unavailable for delivery')
            url=self.origin+'/_matrix/client/v3/rooms/'+quote(room_id,safe='')+'/send/'+quote(message_type,safe='')+'/'+quote(tx_id,safe='')
            async with self.http.put(url,json=content,headers={'Authorization':'Bearer '+token},allow_redirects=False,timeout=10) as response:
                raw=await response.content.read(131073)
                if len(raw)>131072:raise RuntimeError('Oversize Matrix response')
                import json
                try:data=json.loads(raw)
                except (ValueError,UnicodeDecodeError):data={}
                if not isinstance(data,dict):raise RuntimeError('Invalid Matrix response object')
                if response.status==429 or data.get('errcode')=='M_LIMIT_EXCEEDED':
                    value=data.get('retry_after_ms')
                    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<0:
                        try:value=float(response.headers.get('Retry-After','5'))*1000
                        except ValueError:value=5000
                    if not math.isfinite(value) or value<0:value=5000
                    delay=max(.05,value/1000);self.not_before=self.clock()+delay;self.metrics['rate_limited']+=1
                    raise DeliveryDeferred(delay,server_rate_limit=True)
                ident=data.get('event_id')
                if response.status!=200 or not isinstance(ident,str) or not ident:raise RuntimeError('Matrix delivery was not confirmed')
                self.metrics['sent']+=1;return RoomSendResponse(ident,room_id)


class TypingIndicators:
    def __init__(self,client,*,delay=.12,timeout=.25):
        self.client,self.delay,self.timeout=client,delay,timeout;self.rooms={};self.tasks=set()

    def _spawn(self,coro):
        task=asyncio.create_task(coro);self.tasks.add(task);task.add_done_callback(self.tasks.discard);return task

    async def _send(self,room,state):
        try:await asyncio.wait_for(self.client.room_typing(room,typing_state=state,timeout=30000),self.timeout)
        except (Exception,asyncio.CancelledError):pass

    def start(self,room):
        if not hasattr(self.client,'room_typing'):return
        state=self.rooms.setdefault(room,{'count':0,'task':None,'attempted':False})
        state['count']+=1
        if state['count']>1:return
        if state['task']:state['task'].cancel()
        async def later():
            try:
                await asyncio.sleep(self.delay)
                if state['count']:
                    state['attempted']=True;await self._send(room,True)
            except asyncio.CancelledError:pass
        state['task']=self._spawn(later())

    def finish(self,room):
        state=self.rooms.get(room)
        if not state:return
        state['count']=max(0,state['count']-1)
        if state['count']:return
        if state['task']:state['task'].cancel()
        async def stop():
            if state['attempted'] and not state['count']:await self._send(room,False)
            if not state['count'] and self.rooms.get(room) is state:self.rooms.pop(room,None)
        state['task']=self._spawn(stop())

    async def close(self):
        for task in list(self.tasks):task.cancel()
        if self.tasks:await asyncio.gather(*list(self.tasks),return_exceptions=True)
        self.rooms.clear()
