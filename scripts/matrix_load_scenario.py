#!/usr/bin/env python3
"""Synthetic private-room Matrix load scenario, run only in an isolated VM harness."""
import asyncio,json,math,os,sqlite3,time,uuid,sys
from pathlib import Path
from urllib.parse import quote
import aiohttp
sys.path.insert(0,'/app')
from evaluation_core import private_json

async def scenario(stage):
    credentials=json.loads((stage/'load-credentials.json').read_text());gen=credentials['generator'];bot=credentials['bot'];rooms=[];since=None;answers={};max_pending=0
    base='http://homeserver:8008';counter=0
    report={'mode':'isolated_real_matrix_synapse_direct','synthetic_users':10,'private_rooms':2,'cohorts':[],'cleanup':{}}
    users=[credentials['user'+str(i)] for i in range(10)]
    async with aiohttp.ClientSession() as http:
        async def api(method,path,body=None,token=None):
            for attempt in range(12):
                async with http.request(method,base+path,json=body,headers={'Authorization':'Bearer '+(token or gen['access_token'])},timeout=aiohttp.ClientTimeout(total=30)) as res:
                    data=await res.json()
                    if res.status==429:
                        await asyncio.sleep(min(10,data.get('retry_after_ms',1000)/1000)+.02);continue
                    if res.status>=400:raise RuntimeError('Matrix HTTP '+str(res.status)+' '+str(data.get('errcode')))
                    return data
            raise RuntimeError('Matrix rate-limit retry budget reached')
        async def control(action,workers=2,phase="recovery"):
            nonce=uuid.uuid4().hex;p=stage/'load-control.json';temporary=p.with_name(p.name+'.'+nonce);temporary.write_text(json.dumps({'action':action,'workers':workers,'nonce':nonce,'phase':phase}));temporary.chmod(0o644);os.replace(temporary,p)
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                ack=stage/'load-ack.json'
                if ack.exists() and json.loads(ack.read_text()).get('nonce')==nonce:return
                await asyncio.sleep(.1)
            raise RuntimeError('Isolated bot control timed out')
        def pending():
            nonlocal max_pending
            try:
                db=sqlite3.connect('file:/loadstate/matrix_inbox.sqlite3?mode=ro',uri=True);n=db.execute("SELECT count(*) FROM inbox_requests WHERE state!='done'").fetchone()[0];db.close();max_pending=max(max_pending,n);return n
            except sqlite3.Error:return 0
        async def sync():
            nonlocal since
            data=await api('GET','/_matrix/client/v3/sync?timeout=1000&filter='+quote(json.dumps({'room':{'timeline':{'limit':100}}}))+('&since='+quote(since) if since else ''));since=data['next_batch'];pending()
            for room,j in data.get('rooms',{}).get('join',{}).items():
                for event in j.get('timeline',{}).get('events',[]):
                    if event.get('sender')!=bot['user_id'] or event.get('type')!='m.room.message':continue
                    c=event['content'];request=c.get('org.sts.request_id')
                    if request and not c.get('body','').startswith('Готовлю ответ'):
                        answers.setdefault(request,{})[event['event_id']]={'observed':time.monotonic(),'generation_error':'Сервис генерации временно недоступен' in c.get('body',''),'feedback_saved':'Оценка сохранена' in c.get('body',''),'feedback_updated':'заменена новой' in c.get('body',''),'feedback_duplicate':'учтена один раз' in c.get('body','')}
        async def send(room,question,account=None):
            start=time.monotonic();r=await api('PUT','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/send/m.room.message/'+uuid.uuid4().hex,{'msgtype':'m.text','body':question},token=(account or gen)['access_token']);return r['event_id'],start
        async def wait_for(ids):
            deadline=time.monotonic()+600
            while any(i not in answers for i in ids) and time.monotonic()<deadline:await sync()
            if any(i not in answers for i in ids):raise RuntimeError('Load questions timed out')
        def pct(values,p):return round(sorted(values)[max(0,math.ceil(len(values)*p)-1)],3) if values else None
        try:
            for i in range(2):
                # Keep room creation separate from invites: createRoom is not idempotent.
                r=await api('POST','/_matrix/client/v3/createRoom',{'name':'STS isolated load '+str(i),'preset':'private_chat'});rooms.append(r['room_id'])
                await api('POST','/_matrix/client/v3/rooms/'+quote(rooms[i],safe='')+'/invite',{'user_id':bot['user_id']})
            for i,account in enumerate(users):
                room=rooms[i%2]
                await api('POST','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/invite',{'user_id':account['user_id']})
                await api('POST','/_matrix/client/v3/join/'+quote(room,safe=''),{},token=account['access_token'])
            print('Isolated users and two private rooms ready',flush=True)
            private_json(Path('/loadstate/room_routes.json'),{r:('legal' if i%2==0 else 'finance') for i,r in enumerate(rooms)})
            await control('start',1,phase='mixed_baseline')
            # Confirm bot membership; request publication starts only after all rooms are joined.
            deadline=time.monotonic()+180
            for room in rooms:
                while True:
                    members=await api('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/joined_members')
                    if bot['user_id'] in members['joined']:break
                    if time.monotonic()>deadline:raise RuntimeError('Load bot did not join private rooms')
                    await asyncio.sleep(.2)
            await sync()
            mixed=['Начальник не отдаёт деньги за работу','Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?',
                   'Можно ли отозвать согласие на обработку персональных данных?','Что такое ГЭСВ и чем отличается от номинальной ставки?',
                   'Что регулирует брачный договор?','Что страхуется по обязательному автострахованию: машина или ответственность?',
                   'Работодатель заставляет уйти в отпуск без содержания. Обязан соглашаться?','Какие комиссии и страховки включаются в ГЭСВ?',
                   'Что считается валютной операцией по закону?','Какие полномочия у органов валютного контроля?']
            for label,count,workers in [('mixed_baseline',10,1),('mixed_parallel',10,2),('source_bound_burst',20,2)]:
                if label!='mixed_baseline':await control('restart',workers,phase=label)
                max_pending=0;started=time.monotonic()
                questions=mixed if label!='source_bound_burst' else ['Начальник не отдаёт деньги за работу' if i%2==0 else 'Что такое ГЭСВ и чем отличается от номинальной ставки?' for i in range(20)]
                submitted=await asyncio.gather(*(send(rooms[i%2],questions[i],users[i%10]) for i in range(count)))
                ingress=time.monotonic()-started;ids=[p[0] for p in submitted];await wait_for(ids);await asyncio.sleep(.1)
                db=sqlite3.connect('file:/loadstate/matrix_inbox.sqlite3?mode=ro',uri=True)
                metrics=[db.execute('SELECT started-received,finished-started,attempts,response_ready-started,finished-response_ready,delivery_attempts,delivery_deferrals,delivery_rate_limits FROM inbox_requests WHERE event_id=?',(i,)).fetchone() for i in ids];db.close()
                latencies=[next(iter(answers[i].values()))['observed']-submitted[j][1] for j,i in enumerate(ids)]
                row={'label':label,'requests':count,'workers':workers,'inference':'mixed_actual_model_and_source_bound' if label!='source_bound_burst' else 'source_bound_no_new_model_calls',
                     'ingress_seconds':round(ingress,3),'drain_seconds':round(time.monotonic()-started,3),'max_pending_observed':max_pending,
                     'latency_p50_seconds':pct(latencies,.5),'latency_p95_seconds':pct(latencies,.95),
                     'queue_wait_p95_seconds':pct([r[0] for r in metrics if r and r[0] is not None],.95),
                     'service_p95_seconds':pct([r[1] for r in metrics if r and r[1] is not None],.95),
                     'preparation_p95_seconds':pct([r[3] for r in metrics if r and r[3] is not None],.95),
                     'delivery_p95_seconds':pct([r[4] for r in metrics if r and r[4] is not None],.95),
                     'delivery_attempts':sum(r[5] for r in metrics if r),'delivery_deferrals':sum(r[6] for r in metrics if r),'server_rate_limits':sum(r[7] for r in metrics if r),
                     'answered':sum(i in answers for i in ids),'duplicates':sum(max(0,len(answers[i])-1) for i in ids),
                     'generation_errors':sum(next(iter(answers[i].values()))['generation_error'] for i in ids)}
                report['cohorts'].append(row);private_json(stage/'matrix-load-results.json',report);print(json.dumps(row),flush=True)
            await control('stop');offline=[await send(rooms[0],mixed[0],users[0]),await send(rooms[1],mixed[1],users[1])];await control('start',2);await wait_for([x[0] for x in offline]);await control('restart',2)
            until=time.monotonic()+10
            while time.monotonic()<until:await sync()
            report['recovery']={'offline_answers':len(offline),'reply_counts':[len(answers[x[0]]) for x in offline],'pending':pending()}
            # Voluntary feedback is tested only for these synthetic people.
            feedback_submitted=[]
            for i,room,command in [(0,rooms[0],'!оценка полезно'),(1,rooms[1],'!оценка полезно'),(0,rooms[0],'!оценка ошибка'),(0,rooms[0],'!оценка ошибка')]:
                request=await send(room,command,users[i]);feedback_submitted.append(request[0]);await wait_for([request[0]])
            flags=[next(iter(answers[i].values())) for i in feedback_submitted]
            db=sqlite3.connect('file:/loadstate/matrix_inbox.sqlite3?mode=ro',uri=True)
            feedback_rows=db.execute('SELECT domain,category,count(*) FROM answer_feedback GROUP BY domain,category').fetchall();db.close()
            report['feedback']={'acknowledged':len(flags),'saved_acknowledgements':sum(x['feedback_saved'] for x in flags),
                'updated_acknowledgements':sum(x['feedback_updated'] for x in flags),'duplicate_acknowledgements':sum(x['feedback_duplicate'] for x in flags),
                'records':sum(r[2] for r in feedback_rows),'categories':{d:{c:n for d2,c,n in feedback_rows if d2==d} for d,_,_ in feedback_rows}}
            assert report['feedback']['records']==2 and report['feedback']['categories']=={'legal':{'suspected_error':1},'finance':{'helpful':1}}
            assert report['feedback']['duplicate_acknowledgements']==1
            private_json(stage/'matrix-load-results.json',report)
        finally:
            try:await control('stop')
            except Exception as exc:report['cleanup']['stop_error']=type(exc).__name__
            for name,account in credentials.items():
                try:
                    login=await api('POST','/_matrix/client/v3/login',{'type':'m.login.password','identifier':{'type':'m.id.user','user':account['user_id']},'password':account['password']})
                    await api('POST','/_matrix/client/v3/account/deactivate',{'auth':{'type':'m.login.password','identifier':{'type':'m.id.user','user':account['user_id']},'password':account['password']},'erase':True},token=login['access_token'])
                    report['cleanup'][name+'_deactivated']=True
                except Exception as exc:report['cleanup'][name+'_error']=type(exc).__name__
            private_json(stage/'matrix-load-results.json',report)
    if any(r['duplicates'] or r['generation_errors'] or r['answered']!=r['requests'] for r in report['cohorts']):raise RuntimeError('Load cohort failures')
    if report.get('recovery',{}).get('reply_counts')!=[1,1]:raise RuntimeError('Recovery failure')
    print('ISOLATED_MATRIX_LOAD_COMPLETE',flush=True)
if __name__=='__main__':asyncio.run(scenario(Path(os.environ.get('STS_LOAD_STAGE','/stage'))))
