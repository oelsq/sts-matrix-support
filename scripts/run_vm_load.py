#!/usr/bin/env python3
"""Run bounded load on an isolated Matrix bot; never stop/reconfigure the working bot."""
import argparse,json,os,secrets,subprocess,time,re,tempfile
from collections import Counter
from pathlib import Path

def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def validate_stage(stage,allowed_root=Path('/home/admin_sts/sts-matrix-support/stages')):
    stage=Path(stage).resolve();allowed_root=Path(allowed_root).resolve()
    if stage==allowed_root or not stage.is_relative_to(allowed_root):raise ValueError('Use a dedicated project stage directory')
    if any((stage/name).exists() for name in ['load-credentials.json','matrix-load-results.json','golden-live.json','load-observations.json','matrix-answer-checks.json']):
        raise ValueError('Stage contains previous test artifacts; use a fresh directory')
    return stage

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True);p.add_argument('--candidate',required=True);p.add_argument('--knowledge',type=Path);p.add_argument('--semantic-root',type=Path);a=p.parse_args()
    a.candidate=subprocess.check_output(['docker','image','inspect',a.candidate,'--format','{{.Id}}'],text=True).strip()
    try:stage=validate_stage(a.stage)
    except ValueError as exc:p.error(str(exc))
    stage.mkdir(parents=True,exist_ok=True);project=Path('/home/admin_sts/sts-matrix-support/project').resolve();state=stage/'loadstate';state.mkdir(mode=0o700,exist_ok=True)
    production=json.loads(subprocess.check_output(['docker','inspect','sts-team-demo-bot-1']))[0]
    production_env=dict(v.split('=',1) for v in production['Config']['Env'])
    key_destination=production_env['LLM_API_KEY_FILE']
    key_mount=next(m for m in production['Mounts'] if m['Destination']==key_destination)
    suffix=secrets.token_hex(3);botname='sts-isolated-load-bot-'+suffix;runner='sts-isolated-load-client-'+suffix;auditor='sts-isolated-load-audit-'+suffix
    register=r'''
import json,secrets,hashlib,hmac,urllib.request,yaml
from pathlib import Path
secret=yaml.safe_load(Path('/data/homeserver.yaml').read_text())['registration_shared_secret']
def req(body=None):
 r=urllib.request.Request('http://127.0.0.1:8008/_synapse/admin/v1/register',data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(r) as f:return json.load(f)
result={}
for kind in ['generator','bot']+['user'+str(i) for i in range(10)]:
 nonce=req()['nonce'];user='sts-load-'+kind+'-'+secrets.token_hex(4);password=secrets.token_urlsafe(30)
 mac=hmac.new(secret.encode(),chr(0).join([nonce,user,password,'notadmin']).encode(),hashlib.sha1).hexdigest()
 result[kind]=dict(req({'nonce':nonce,'username':user,'password':password,'admin':False,'mac':mac}),password=password)
print(json.dumps(result))
'''
    credentials=json.loads(subprocess.check_output(['docker','exec','-i','sts-team-demo-homeserver-1','python','-'],input=register,text=True))
    def private(path,value,mode=0o600):
        fd,name=tempfile.mkstemp(prefix=Path(path).name+'.',dir=Path(path).parent)
        try:
            with os.fdopen(fd,'w') as f:
                os.fchmod(f.fileno(),mode);f.write(value);f.flush();os.fsync(f.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    private(stage/'load-credentials.json',json.dumps(credentials))
    env={'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':credentials['bot']['user_id'],'MATRIX_PASSWORD':credentials['bot']['password'],
         'LLM_ENDPOINT':production_env['LLM_ENDPOINT'],'LLM_MODEL':production_env['LLM_MODEL'],'LEGAL_LLM_MODEL':production_env['LEGAL_LLM_MODEL'],'FINANCE_LLM_MODEL':production_env['FINANCE_LLM_MODEL'],
         'LLM_API_KEY_FILE':key_destination,'KNOWLEDGE_DIR':'/app/offline_knowledge','OFFLINE_MODE':'1','MATRIX_ALLOWED_HOSTS':'homeserver','LLM_ALLOWED_HOSTS':'litellm',
         'MATRIX_ROOM_ROUTES_FILE':'/app/state/room_routes.json','MATRIX_FREE_REPLY_ROOMS_FILE':'/app/state/room_routes.json','MATRIX_INBOX_DB':'/app/state/matrix_inbox.sqlite3','HANDOFF_DB':'/app/state/handoff.sqlite3',
         'LEGAL_DEPARTMENT_USERS':'','FINANCE_DEPARTMENT_USERS':'','PYTHONPATH':'/app:/app/tools'}
    key_mounts=['-v',key_mount['Source']+':'+key_destination+':ro','-v',str((a.knowledge or project/'offline_knowledge').resolve())+':/app/offline_knowledge:ro']
    if a.semantic_root:
        semantic=a.semantic_root.resolve()
        if not all((semantic/n).is_dir() for n in ['model','index']):raise ValueError('Semantic assets missing')
        key_mounts += ['-v',str(semantic)+':/semantic:ro']
        env.update(SEMANTIC_MODEL_DIR='/semantic/model',SEMANTIC_INDEX_DIR='/semantic/index',SEMANTIC_THREADS='2',HF_HUB_OFFLINE='1')
    envfile=stage/'load.env'
    observations=[];current_phase=None
    def create_bot(workers,phase):
        nonlocal current_phase
        current_phase=phase
        private(envfile,'\n'.join(k+'='+v for k,v in dict(env,MATRIX_REQUEST_WORKERS=str(workers)).items())+'\n')
        run(['docker','create','--name',botname,'--network','sts-team-demo_default','--env-file',str(envfile),*key_mounts,'-v',str(state)+':/app/state',a.candidate,'python','/app/bot.py'],stdout=subprocess.DEVNULL)
        run(['docker','network','connect','llm-net',botname]);run(['docker','start',botname],stdout=subprocess.DEVNULL)
        for _ in range(90):
            r=subprocess.run(['docker','logs',botname],capture_output=True,text=True)
            if 'Matrix session ready;' in r.stdout+r.stderr:return
            time.sleep(.5)
        raise RuntimeError('Isolated bot startup timed out')
    def remove_bot():
        logs=subprocess.run(['docker','logs',botname],capture_output=True,text=True)
        if logs.returncode==0:
            text=logs.stdout+logs.stderr
            observations.append({'phase':current_phase,'preparation_mechanisms':dict(Counter(re.findall(r'mechanism=([a-z_]+)',text))),
                'delivery_summary':[json.loads(v) for v in re.findall(r'Matrix delivery summary: (\{[^\n]+\})',text)],'rate_limit_log_lines':sum('429' in line or 'M_LIMIT_EXCEEDED' in line or 'rate limit' in line.casefold() for line in text.splitlines())})
            private(stage/'load-observations.json',json.dumps(observations))
        subprocess.run(['docker','rm','-f',botname],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    proc=None;audit_proc=None;handled=set()
    try:
        # Same candidate and production model route; this evaluation sends no Matrix messages.
        golden=subprocess.run(['docker','run','--rm','--network','llm-net','--env-file',str(envfile),*key_mounts,'-v',str(stage)+':/stage',a.candidate,'python','/app/tools/evaluate_golden.py','run','--live','direct','--output','/stage/golden-live.json'])
        print('Golden evaluation exit:',golden.returncode,flush=True)
        if golden.returncode:raise RuntimeError('Golden evaluation failed; inspect private report before load')
        private(envfile,'\n'.join(k+'='+v for k,v in env.items())+'\n')
        run(['docker','create','--name',runner,'--network','sts-team-demo_default','--env-file',str(envfile),*key_mounts,'-v',str(state)+':/loadstate','-v',str(stage)+':/stage',a.candidate,'python','-u','/app/tools/matrix_load_scenario.py'],stdout=subprocess.DEVNULL)
        run(['docker','create','--name',auditor,'--network','sts-team-demo_default','-v',str(stage)+':/stage',a.candidate,'python','-u','/app/tools/evaluate_matrix_load_answers.py'],stdout=subprocess.DEVNULL)
        audit_proc=subprocess.Popen(['docker','start','-a',auditor])
        run(['docker','network','connect','llm-net',runner]);proc=subprocess.Popen(['docker','start','-a',runner]);deadline=time.monotonic()+1800
        while proc.poll() is None:
            if time.monotonic()>deadline:raise RuntimeError('Load harness deadline')
            control=stage/'load-control.json'
            if control.exists():
                value=json.loads(control.read_text());nonce=value['nonce']
                if nonce not in handled:
                    action=value['action'];workers=value['workers']
                    if action not in {'start','stop','restart'} or workers not in {1,2}:raise ValueError('Unexpected load control')
                    remove_bot()
                    if action!='stop':create_bot(workers,value.get('phase'))
                    ack=stage/'load-ack.json';private(ack,json.dumps({'nonce':nonce}),mode=0o644);handled.add(nonce)
            time.sleep(.1)
        if proc.returncode:raise RuntimeError('Load client failed; private report retained')
        if audit_proc.wait(timeout=20):raise RuntimeError('Synthetic answer checks incomplete or failed')
    finally:
        remove_bot()
        subprocess.run(['docker','rm','-f',runner],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['docker','rm','-f',auditor],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        envfile.unlink(missing_ok=True)
        # Cleanup also runs when the golden preflight fails before client startup.
        cleanup_code="import json,urllib.request,urllib.error,os\nfrom pathlib import Path\nstage=Path('/stage');creds=json.loads((stage/'load-credentials.json').read_text());p=stage/'matrix-load-results.json'\nr=json.loads(p.read_text()) if p.exists() else {'cohorts':[],'cleanup':{}}\nfor kind,a in creds.items():\n if r['cleanup'].get(kind+'_deactivated'):continue\n if not a['user_id'].startswith('@sts-load-'+kind+'-'):raise ValueError('Not an isolated test account')\n body={'auth':{'type':'m.login.password','identifier':{'type':'m.id.user','user':a['user_id']},'password':a['password']},'erase':True}\n req=urllib.request.Request('http://homeserver:8008/_matrix/client/v3/account/deactivate',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+a['access_token']})\n try:\n  with urllib.request.urlopen(req,timeout=20) as f:json.load(f)\n  r['cleanup'][kind+'_deactivated']=True\n except Exception as e:r['cleanup'][kind+'_error']=type(e).__name__\nfd=os.open(p,os.O_WRONLY|os.O_TRUNC|os.O_CREAT,0o600)\nwith os.fdopen(fd,'w') as f:json.dump(r,f)\nprint(json.dumps(r['cleanup']))\nif all(r['cleanup'].get(k+'_deactivated') for k in creds):(stage/'load-credentials.json').unlink()\nelse:raise RuntimeError('Some synthetic accounts need cleanup')\n"
        cleanup_run=subprocess.run(['docker','run','--rm','--network','sts-team-demo_default','-v',str(stage)+':/stage',a.candidate,'python','-c',cleanup_code])
        if cleanup_run.returncode==0:
            run(['docker','run','--rm','-v',str(state)+':/state',a.candidate,'python','-c',"from pathlib import Path;[p.unlink() for p in Path('/state').glob('matrix_inbox.sqlite3*')];[p.unlink() for p in Path('/state').glob('handoff.sqlite3*')]"],stdout=subprocess.DEVNULL)
        for f in ['load-control.json','load-ack.json']:(stage/f).unlink(missing_ok=True)
    print('Isolated load completed; working bot never stopped',flush=True)
if __name__=='__main__':main()
