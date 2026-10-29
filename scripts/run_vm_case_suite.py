"""Launch a finite synthetic Matrix campaign without changing the working bot."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time


def main():
    p=argparse.ArgumentParser(); p.add_argument('--stage',type=Path,required=True); p.add_argument('--candidate')
    args=p.parse_args(); stage=args.stage.resolve()
    root=Path('/home/admin_sts/sts-matrix-support/stages')
    if stage==root or not stage.is_relative_to(root): raise ValueError('Dedicated stage required')
    if (stage/'load-credentials.json').exists() or (stage/'matrix-case-results.json').exists():
        raise ValueError('Use a fresh stage; previous credentials/results must be preserved')
    stage.mkdir(parents=True,exist_ok=True); stage.chmod(0o700)
    (stage/'state').mkdir(mode=0o700,exist_ok=True)
    original=json.loads(subprocess.check_output(['docker','inspect','sts-team-demo-bot-1']))[0]
    production_image=original['Image']
    image=subprocess.check_output(['docker','image','inspect',args.candidate or production_image,'--format','{{.Id}}'],text=True).strip()
    suffix=secrets.token_hex(4)
    botname='sts-cases-bot-'+suffix; clientname='sts-cases-client-'+suffix
    original_env=dict(v.split('=',1) for v in original['Config']['Env'])
    setting_names={
        'MATRIX_HOMESERVER','MATRIX_USER','MATRIX_PASSWORD','OFFLINE_MODE','MATRIX_ALLOWED_HOSTS',
        'LLM_ENDPOINT','LLM_MODEL','LEGAL_LLM_MODEL','FINANCE_LLM_MODEL','GENERAL_LLM_MODEL',
        'LLM_API_KEY_FILE','LLM_ALLOWED_HOSTS','KNOWLEDGE_DIR','FLOWISE_URL','FLOWISE_ALLOWED_HOSTS',
        'FLOWISE_API_KEY_FILE','FLOWISE_ROOM_ROUTES_FILE','SEMANTIC_MODEL_DIR','SEMANTIC_INDEX_DIR',
        'SEMANTIC_THREADS','HF_HUB_OFFLINE','PYTHONPATH','ELEMENT_PUBLIC_URL',
    }
    env={k:v for k,v in original_env.items() if k in setting_names}
    def private(path,value):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:f.write(value)
    register=r'''
import json,secrets,hashlib,hmac,urllib.request,yaml
from pathlib import Path
secret=yaml.safe_load(Path('/data/homeserver.yaml').read_text())['registration_shared_secret']
def req(body=None):
 r=urllib.request.Request('http://127.0.0.1:8008/_synapse/admin/v1/register',data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=30) as f:return json.load(f)
for kind in ['generator','bot']+['user'+str(i) for i in range(4)]:
 nonce=req()['nonce'];user='sts-cases-'+kind+'-'+secrets.token_hex(4);password=secrets.token_urlsafe(30)
 mac=hmac.new(secret.encode(),chr(0).join([nonce,user,password,'notadmin']).encode(),hashlib.sha1).hexdigest()
 result=dict(req({'nonce':nonce,'username':user,'password':password,'admin':False,'mac':mac}),password=password)
 print(json.dumps({'kind':kind,'record':result}),flush=True)
'''
    creds={}
    # Persist each completed registration so cleanup can handle a partial failure.
    try:
        registration=subprocess.Popen(['docker','exec','-i','sts-team-demo-homeserver-1','python','-'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        registration.stdin.write(register); registration.stdin.close()
        for line in registration.stdout:
            row=json.loads(line);creds[row['kind']]=row['record']
            private(stage/'load-credentials.json',json.dumps(creds))
        if registration.wait():raise RuntimeError('Synthetic registration failed')
        routes=subprocess.check_output(['docker','exec','sts-team-demo-bot-1','cat',
            env['FLOWISE_ROOM_ROUTES_FILE']],text=True)
        private(stage/'flow-routes-template.json',routes)
        # Credentials stay on the same VM and retain the same scope/endpoints.
        env.update(MATRIX_USER=creds['bot']['user_id'],MATRIX_PASSWORD=creds['bot']['password'],
            MATRIX_HOMESERVER='http://homeserver:8008',MATRIX_REQUEST_WORKERS='2',
            FLOWISE_ROOM_ROUTES_FILE='/app/handoff_state/flowise_rooms.json',
            MATRIX_ROOM_ROUTES_FILE='/app/handoff_state/absent.json',
            MATRIX_INBOX_DB='/app/handoff_state/matrix_inbox.sqlite3',
            HANDOFF_DB='/app/handoff_state/handoff.sqlite3',
            LEGAL_DEPARTMENT_USERS='',FINANCE_DEPARTMENT_USERS='',
            LEGAL_DEPARTMENT_USER='',FINANCE_DEPARTMENT_USER='')
        envfile=stage/'suite.env';private(envfile,'\n'.join(k+'='+v for k,v in env.items())+'\n')
        # Mount exactly the corpus, optional semantic assets and two scoped
        # model/Flowise key files. The working state and unrelated mounts stay out.
        mounts=[]
        for setting in ['KNOWLEDGE_DIR','SEMANTIC_MODEL_DIR','SEMANTIC_INDEX_DIR',
                        'LLM_API_KEY_FILE','FLOWISE_API_KEY_FILE']:
            destination=env.get(setting)
            if not destination:continue
            matches=[m for m in original['Mounts'] if destination==m['Destination']
                     or destination.startswith(m['Destination']+'/')]
            if not matches:raise ValueError('Required test asset is not a known working mount')
            item=max(matches,key=lambda m:len(m['Destination']))
            source=Path(item['Source'])/destination[len(item['Destination']):].lstrip('/')
            mounts+=['-v',str(source)+':'+destination+':ro']
        shared=['--network','sts-team-demo_default','--env-file',str(envfile),*mounts,
            '-v',str(stage)+':/stage','-v',str(stage/'state')+':/loadstate']
        subprocess.run(['docker','create','--name',clientname,*shared,image,
            'python','-u','/stage/matrix_case_suite.py'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','network','connect','llm-net',clientname],check=True)
        client=subprocess.Popen(['docker','start','-a',clientname])
        deadline=time.monotonic()+180
        while not (stage/'suite-ready.json').exists():
            if client.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Room setup failed')
            time.sleep(.5)
        subprocess.run(['docker','create','--name',botname,'--network','sts-team-demo_default',
            '--env-file',str(envfile),*mounts,'-v',str(stage/'state')+':/app/handoff_state',
            image,'python','/app/bot.py'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','network','connect','llm-net',botname],check=True)
        subprocess.run(['docker','start',botname],check=True,stdout=subprocess.DEVNULL)
        for _ in range(180):
            status=json.loads(subprocess.check_output(['docker','inspect',botname]))[0]['State']
            if not status['Running']: raise RuntimeError('Isolated bot exited before readiness')
            logs=subprocess.run(['docker','logs',botname],capture_output=True,text=True)
            if 'Matrix session ready;' in logs.stdout+logs.stderr:break
            time.sleep(.5)
        else:raise RuntimeError('Isolated bot readiness timeout')
        print(json.dumps({'campaign_started':True,'tested_image':image,'production_image':production_image,'workers':2}),flush=True)
        try:code=client.wait(timeout=10800)
        except subprocess.TimeoutExpired:raise RuntimeError('Campaign deadline exceeded') from None
        if code:raise RuntimeError('Matrix case client failed; private report retained')
    finally:
        log=subprocess.run(['docker','logs',botname],capture_output=True,text=True)
        if log.returncode==0:
            text=log.stdout+log.stderr
            private(stage/'isolated-bot.log',text)
            observations={'preparation_mechanisms':dict(Counter(re.findall(r'mechanism=([a-z_]+)',text))),
                'agentflow_completed':text.count('Agentflows V2 workflow completed;'),
                'workflows':[json.loads(s) for s in re.findall(r'Flowise workflow summary: (\{[^\n]+\})',text)],
                'working_bot_stopped':False,'tested_image':image,'production_image':production_image}
            private(stage/'suite-observations.json',json.dumps(observations))
        for name in [botname,clientname]:subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cleanup=r'''
from pathlib import Path
import json,urllib.request,time,urllib.error
p=Path('/stage/load-credentials.json');creds=json.loads(p.read_text());done={}
for kind,a in creds.items():
 assert a['user_id'].startswith('@sts-cases-'+kind+'-')
 body={'auth':{'type':'m.login.password','identifier':{'type':'m.id.user','user':a['user_id']},'password':a['password']},'erase':True}
 for attempt in range(15):
  request=urllib.request.Request('http://homeserver:8008/_matrix/client/v3/account/deactivate',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+a['access_token']})
  try:
   with urllib.request.urlopen(request,timeout=30) as response:json.load(response)
   done[kind]=True;break
  except urllib.error.HTTPError as exc:
   if exc.code==429:time.sleep(5);continue
   done[kind]=False;break
  except Exception:done[kind]=False;break
print(json.dumps({'synthetic_accounts_deactivated':done}))
if not all(done.get(k) for k in creds):raise RuntimeError('Synthetic account cleanup pending')
p.unlink()
for f in ['suite.env','flow-routes-template.json']:(Path('/stage')/f).unlink(missing_ok=True)
for f in Path('/stage/state').iterdir():
 if f.is_file():f.unlink()
'''
        if creds:
            result=subprocess.run(['docker','run','--rm','--network','sts-team-demo_default',
                '-v',str(stage)+':/stage',image,'python','-c',cleanup])
            if result.returncode:raise RuntimeError('Cleanup incomplete; private credentials retained')
        after=json.loads(subprocess.check_output(['docker','inspect','sts-team-demo-bot-1']))[0]
        assert after['Image']==production_image and after['State']['Running']
        print(json.dumps({'working_bot_running':True,'working_bot_image_unchanged':True}),flush=True)


if __name__=='__main__':main()
