"""Opt-in testing in primary Matrix rooms; fetch only replies to our own events.

Run inside the existing bot container. No room timeline/sync is read and no
model/staff/rate-limit configuration is modified. Private output stays on VM.
"""
import argparse,asyncio,hashlib,json,os,re,sqlite3,time,uuid,sys
from pathlib import Path
from urllib.parse import quote
import aiohttp
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'matrix_support_bot'))
sys.path.append('/app')
from evaluation_core import private_json,digest,automated_checks

def own_reply_row(db,request_id,sender,room):
    if not sender.startswith('@sts-main-cases-') or not request_id.startswith('$'):
        raise ValueError('Only explicitly synthetic events may be inspected')
    rows=db.execute('''SELECT r.state,r.received,r.started,r.response_ready,r.finished,
        r.delivery_attempts,r.delivery_deferrals,r.delivery_rate_limits,o.sent_id
        FROM inbox_requests r LEFT JOIN inbox_outbox o ON o.event_id=r.event_id AND o.final=1
        WHERE r.event_id=? AND r.sender=? AND r.room_id=?''',(request_id,sender,room)).fetchall()
    if len(rows)>1:raise RuntimeError('multiple_final_outbox_rows')
    return dict(rows[0]) if rows else None

async def run(args):
    if not args.allow_main_rooms:raise ValueError('Explicit primary-room opt-in required')
    credentials=json.loads(args.credentials.read_text());sender=credentials['user_id']
    if not sender.startswith('@sts-main-cases-'):raise ValueError('Dedicated synthetic account required')
    raw=json.loads(args.cases.read_text());cases=raw['cases']
    if args.ids:
        selected=set(args.ids.split(','));assert selected<={c['id'] for c in cases};cases=[c for c in cases if c['id'] in selected]
    if not 1<=len(cases)<=60 or len({c['id'] for c in cases})!=len(cases):raise ValueError('Bounded unique case set required')
    routes=json.loads(args.main_rooms.read_text())
    if len(routes)!=2 or set(routes.values())!={'legal','finance'}:raise ValueError('Expected exactly two primary rooms')
    rooms={domain:room for room,domain in routes.items()}
    flows=json.loads(Path(os.environ['FLOWISE_ROOM_ROUTES_FILE']).read_text())
    if any(flows[room]['domain']!=domain for domain,room in rooms.items()):raise ValueError('Primary route conflict')
    db_path=os.getenv('MATRIX_INBOX_DB',str(Path(os.environ['HANDOFF_DB']).parent/'matrix_inbox.sqlite3'))
    db=sqlite3.connect('file:'+db_path+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
    base=os.environ['MATRIX_HOMESERVER'].rstrip('/');bot=os.environ['MATRIX_USER'];rows=[];halted=asyncio.Event()
    def checkpoint(status='in_progress'):
        private_json(args.output,{'status':status,'mode':'primary_rooms_existing_production_bot','cases_sha256':digest(raw),'rows':rows,'human_specialist_approvals':0,'timeline_read':False})
    async with aiohttp.ClientSession() as http:
        async def api(method,path,body=None):
            for _ in range(15):
                async with http.request(method,base+path,json=body,headers={'Authorization':'Bearer '+credentials['access_token']},timeout=aiohttp.ClientTimeout(total=35),allow_redirects=False) as r:
                    value=await r.json()
                    if r.status==429:
                        await asyncio.sleep(min(20,max(.1,value.get('retry_after_ms',1000)/1000))+.1);continue
                    if r.status>=400:raise RuntimeError('matrix_http_'+str(r.status))
                    return value
            raise RuntimeError('matrix_rate_limit_budget')
        who=await api('GET','/_matrix/client/v3/account/whoami')
        if who.get('user_id')!=sender:raise ValueError('Synthetic token identity mismatch')
        for domain,room in rooms.items():
            name=(await api('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state/m.room.name/'))['name']
            if re.search(r'тест|test|synthetic',name,re.I):raise ValueError('Test room is not a primary room')
            if ('ЮрАгент' if domain=='legal' else 'ФинАгент') not in name:raise ValueError('Primary specialist name mismatch')
        async def send(room,text):
            start=time.monotonic();result=await api('PUT','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/send/m.room.message/'+uuid.uuid4().hex,{'msgtype':'m.text','body':text})
            request_id=result['event_id'];deadline=start+300
            while time.monotonic()<deadline:
                status=own_reply_row(db,request_id,sender,room)
                if status and status['sent_id']:
                    event=await api('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/event/'+quote(status['sent_id'],safe=''))
                    content=event['content']
                    if event['sender']!=bot or content.get('org.sts.request_id')!=request_id:raise RuntimeError('reply_correlation_failed')
                    return request_id,content,round(time.monotonic()-start,3),status
                await asyncio.sleep(.8)
            raise RuntimeError('reply_timeout')
        async def worker(domain):
            for case in cases:
                if case['domain']!=domain or halted.is_set():continue
                room=rooms[domain];row={'id':case['id'],'case_sha256':digest(case),'domain':domain,'question':case['question'],'answer':None,'error':None}
                try:
                    await send(room,'!новый')
                    history=[]
                    for previous in case.get('previous_questions',[]):
                        _,body,seconds,_=await send(room,previous);history.append({'question':previous,'answer':body['body'],'seconds':seconds})
                    request,body,seconds,timing=await send(room,case['question']);answer=body.get('body','')
                    row.update(answer=answer,formatted_body=body.get('formatted_body'),history=history,request_id=request,seconds=seconds,
                               generation_unavailable='Сервис генерации временно недоступен' in answer,
                               validation_fallback='ответ не прошёл проверку подтверждений' in answer,
                               rich_html=body.get('format')=='org.matrix.custom.html',private_timing=timing,
                               checks=automated_checks(case,answer,[]))
                except Exception as exc:
                    row['error']=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
                    if row['error'] in {'reply_timeout','reply_correlation_failed'}:halted.set()
                rows.append(row);checkpoint()
                print(json.dumps({'id':row['id'],'completed':len(rows),'total':len(cases),'domain':domain,'answered':row['answer'] is not None,'passed':row.get('checks',{}).get('passed'),'missing':row.get('checks',{}).get('missing_conditions',[]),'fallback':row.get('validation_fallback'),'error':row['error'],'seconds':row.get('seconds')}),flush=True)
        await asyncio.gather(worker('legal'),worker('finance'))
    db.close();checkpoint('complete' if len(rows)==len(cases) else 'incomplete')
    print(json.dumps({'completed':len(rows),'answered':sum(r['answer'] is not None for r in rows),'criteria_passed':sum(r.get('checks',{}).get('passed',False) for r in rows),'primary_rooms':2,'timeline_read':False}),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--main-rooms',required=True,type=Path);p.add_argument('--credentials',required=True,type=Path);p.add_argument('--cases',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--ids');p.add_argument('--allow-main-rooms',action='store_true');args=p.parse_args()
    if args.output.exists():raise ValueError('Preserve previous campaign; use a new output path')
    asyncio.run(run(args))
if __name__=='__main__':main()
