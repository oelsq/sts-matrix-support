#!/usr/bin/env python3
"""Real durable-queue test with simulated inference, isolated from Matrix/LLM services."""
import argparse,asyncio,json,math,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from nio import RoomSendResponse
from matrix_inbox import InboxStore
from matrix_delivery import RequestDispatcher
from evaluation_core import private_json

def percentile(values,p):
    return round(sorted(values)[max(0,math.ceil(len(values)*p)-1)],4) if values else None

async def cohort(count,workers,delay):
    with tempfile.TemporaryDirectory(prefix='sts-queue-load-') as d:
        store=InboxStore(Path(d)/'inbox.db','synthetic-load');delivered={};ready=asyncio.Event();ready.set();started=time.monotonic()
        class Matrix:
            rooms={}
            async def room_send(self,**kwargs):
                delivered.setdefault(kwargs['tx_id'],kwargs['content']);return RoomSendResponse('$'+kwargs['tx_id'],'synthetic')
        async def handle(room,event):
            await asyncio.sleep(delay);await dispatch.send(room.room_id,'m.room.message',{'msgtype':'m.text','body':'Synthetic reply'})
        def fill():
            for n in range(count):
                with store.db:store.enqueue('room-'+str(n%20),NS(event_id='$'+str(n),sender='user-'+str(n%20),body='synthetic',server_timestamp=int(time.time()*1000)+1))
        fill();dispatch=RequestDispatcher(store,Matrix(),handle);task=asyncio.create_task(dispatch.run(ready,workers=workers))
        await asyncio.sleep(delay*1.2);task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        before=store.pending();store.close()
        store=InboxStore(Path(d)/'inbox.db','synthetic-load');dispatch=RequestDispatcher(store,Matrix(),handle)
        task=asyncio.create_task(dispatch.run(ready,workers=workers))
        deadline=time.monotonic()+max(10,count*delay*3)
        while store.pending() and time.monotonic()<deadline:await asyncio.sleep(.005)
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        durations=list(store.db.execute("SELECT started-received,finished-started,finished-received FROM inbox_requests WHERE state='done'"))
        row={'requests':count,'workers':workers,'inference':'simulated_async_delay','delay_seconds':delay,'pending_at_restart':before,'delivered_unique':len(delivered),'remaining':store.pending(),'elapsed_seconds':round(time.monotonic()-started,4),
            'queue_p50_seconds':percentile([x[0] for x in durations],.5),'queue_p95_seconds':percentile([x[0] for x in durations],.95),'total_p95_seconds':percentile([x[2] for x in durations],.95),'duplicates_observed':len(delivered)!=count}
        store.close();return row
async def main(args):
    rows=[]
    for count in args.requests:
        for workers in [1,2]:rows.append(await cohort(count,workers,args.delay))
    private_json(args.output,{'mode':'isolated_queue_simulation_not_live_model_capacity','cohorts':rows});print(json.dumps(rows))
    if any(r['remaining'] or r['delivered_unique']!=r['requests'] for r in rows):raise SystemExit(1)
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--requests',nargs='+',type=int,default=[20,50,100]);p.add_argument('--delay',type=float,default=.02);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if any(not 1<=n<=500 for n in a.requests) or not .001<=a.delay<=1:p.error('Use 1..500 requests and bounded simulated delay')
    asyncio.run(main(a))
