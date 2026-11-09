#!/usr/bin/env python3
"""Prepare and provision an independent demo; never export existing user data."""
import argparse,hashlib,hmac,json,os,secrets,subprocess,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ENV=ROOT/'.env'
RUNTIME=ROOT/'runtime'
def private_write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(text)
    path.chmod(0o600)
def settings():
    result={}
    for line in ENV.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key,value=line.split('=',1);result[key.strip()]=value.strip().strip('\"').strip("'")
    return result

def compose(*args):
    env = settings() if ENV.exists() else {}
    command = ['docker', 'compose', '-f', 'docker-compose.yml']
    if env.get('MATRIX_E2EE_ENABLED')=='1':command += ['-f','compose.secure.yml']
    if env.get('OFFLINE_MODE') == '1':
        command += ['-f', 'compose.offline.yml']
        # Avoid an implicit registry pull during account provisioning/init.
        if args and args[0] == 'up' and '--no-build' not in args:
            args = ('up', '--pull', 'never', '--no-build', *args[1:])
        if args and args[0] == 'run':
            args = ('run', '--pull', 'never', *args[1:])
    subprocess.run([*command, *args], cwd=ROOT, check=True)

def api(base,method,path,data=None,token=None):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request(base+path,method=method,headers=headers,data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None)
    for attempt in range(10):
        try:
            with urllib.request.urlopen(request,timeout=20) as r:return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code!=429:raise
            delay=min(45,max(1,json.load(exc).get('retry_after_ms',5000)/1000+.5))
            print('Matrix rate limit; retrying in',round(delay,1),'seconds',flush=True);time.sleep(delay)
    raise RuntimeError('Rate limit did not clear')

def initialize():
    RUNTIME.mkdir(exist_ok=True)
    for part in ['bot','secrets','synapse']:(RUNTIME/part).mkdir(exist_ok=True)
    (RUNTIME/'secrets').chmod(0o700)
    text=ENV.read_text() if ENV.exists() else (ROOT/'.env.example').read_text()
    for key,value in [('POSTGRES_PASSWORD',secrets.token_urlsafe(24)),('MATRIX_BOT_PASSWORD',secrets.token_urlsafe(24)),('LITELLM_MASTER_KEY','sk-'+secrets.token_urlsafe(32))]:
        text=text.replace(key+'=GENERATE_LOCALLY',key+'='+value)
    private_write(ENV,text)
    secret_path=RUNTIME/'secrets/registration_shared_secret'
    if not secret_path.exists():private_write(secret_path,secrets.token_urlsafe(32))
    # The virtual key is provisioned before the bot is started. Ensure a file mount.
    key_path=RUNTIME/'secrets/litellm_key'
    if not key_path.exists():private_write(key_path,'')
    state_key=RUNTIME/'secrets/state_key'
    if not state_key.exists():
        import base64
        private_write(state_key,base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    (RUNTIME/'bot').chmod(0o700)
    env=settings()
    element={'default_server_config':{'m.homeserver':{'base_url':env['ELEMENT_PUBLIC_URL'],'server_name':env['MATRIX_SERVER_NAME']}},'brand':'STS Support','disable_guests':True}
    (RUNTIME/'element-config.json').write_text(json.dumps(element,ensure_ascii=False,indent=2))
    if not (RUNTIME/'synapse/homeserver.yaml').exists():
        compose('run','--rm','--no-deps','homeserver','generate')
    source='''from pathlib import Path
import yaml,os
p=Path('/data/homeserver.yaml');d=yaml.safe_load(p.read_text())
assert d['server_name']==os.environ['SYNAPSE_SERVER_NAME'],'Do not change server_name for an existing homeserver'
d['enable_registration']=False
d['registration_shared_secret']=Path('/bootstrap/registration_shared_secret').read_text().strip()
d.setdefault('rc_login',{})['address']={'per_second':0.1,'burst_count':30}
p.write_text(yaml.safe_dump(d,allow_unicode=True,sort_keys=False))
print('Synapse configuration prepared')
'''
    compose('run','--rm','--no-deps','--entrypoint','python','homeserver','-c',source)
    print('Initialized. Configure the provider in .env (see docs/OFFLINE.md), then run: python3 scripts/setup.py start')

def wait_for(base,path,token=None):
    end=time.monotonic()+240
    while time.monotonic()<end:
        try:return api(base,'GET',path,token=token)
        except (urllib.error.URLError,TimeoutError):time.sleep(2)
    raise RuntimeError('Service did not become ready: '+base+path)

def provision():
    from urllib.parse import quote
    env=settings();matrix='http://127.0.0.1:'+env['MATRIX_PORT']
    wait_for(matrix,'/_matrix/client/versions')
    # Gateway is addressed through an ephemeral helper in the Docker network;
    # only an internal port is exposed by default.
    gateway_script='''import json,urllib.request,time,os
from pathlib import Path
key=os.environ['LITELLM_MASTER_KEY']
for i in range(120):
 try:
  req=urllib.request.Request('http://litellm:4000/key/generate',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},data=json.dumps({'models':['legal-chat','legal-chat-deepseek','legal-chat-kimi','legal-chat-gptoss'],'key_alias':'sts-element-support'}).encode())
  with urllib.request.urlopen(req,timeout=15) as r: result=json.load(r)
  p=Path('/out/litellm_key');p.write_text(result['key']);p.chmod(0o600)
  print('Restricted LiteLLM key created');break
 except Exception:
  time.sleep(2)
else:raise RuntimeError('LiteLLM virtual-key provisioning failed; inspect gateway logs')
'''
    key_path=RUNTIME/'secrets/litellm_key'
    if not key_path.read_text().strip():
        os.environ['LITELLM_MASTER_KEY']=env['LITELLM_MASTER_KEY']
        compose('run','--rm','--no-deps','-v',str(RUNTIME/'secrets')+':/out','-e','LITELLM_MASTER_KEY','--entrypoint','python','bot','-c',gateway_script)
    saved_path=RUNTIME/'accounts.json'
    saved=json.loads(saved_path.read_text()) if saved_path.exists() else {}
    shared=(RUNTIME/'secrets/registration_shared_secret').read_text().strip()
    accounts=[('bot','ИИ-помощник'),('ruslan','Руслан — тестовый пользователь')]
    for prefix,label in [('jurdep','ЮрДеп'),('findep','ФинДеп')]:
        accounts += [(prefix+(str(i) if i>1 else ''),label+' — сотрудник '+str(i)) for i in range(1,6)]
    tokens={}
    try:
        for username,display in accounts:
            if username not in saved:
                saved[username]={'user_id':'@'+username+':'+env['MATRIX_SERVER_NAME'],'password':env['MATRIX_BOT_PASSWORD'] if username=='bot' else secrets.token_urlsafe(24),'registered':False}
                private_write(saved_path,json.dumps(saved,ensure_ascii=False,indent=2))
            record=saved[username]
            if record['registered']:
                result=api(matrix,'POST','/_matrix/client/v3/login',{'type':'m.login.password','identifier':{'type':'m.id.user','user':record['user_id']},'password':record['password'],'initial_device_display_name':'STS setup'})
            else:
                nonce=api(matrix,'GET','/_synapse/admin/v1/register')['nonce']
                mac=hmac.new(shared.encode(),'\0'.join([nonce,username,record['password'],'notadmin']).encode(),hashlib.sha1).hexdigest()
                try:
                    result=api(matrix,'POST','/_synapse/admin/v1/register',{'nonce':nonce,'username':username,'password':record['password'],'displayname':display,'admin':False,'mac':mac})
                except urllib.error.HTTPError as exc:
                    if exc.code!=400:raise
                    result=api(matrix,'POST','/_matrix/client/v3/login',{'type':'m.login.password','identifier':{'type':'m.id.user','user':record['user_id']},'password':record['password']})
                record['registered']=True;private_write(saved_path,json.dumps(saved,ensure_ascii=False,indent=2))
            tokens[username]=result['access_token']
        routes_path=RUNTIME/'bot/room_routes.json'
        if not routes_path.exists():
            routes={}
            for domain,label in [('legal','ЮрАгент — право'),('finance','ФинАгент — финансы')]:
                room=api(matrix,'POST','/_matrix/client/v3/createRoom',{'name':'STS — '+label,'topic':'Демонстрационная комната с ИИ. Для сотрудника: !юрдеп или !финдеп. Без E2EE.','preset':'private_chat','creation_content':{'m.federate':False},'invite':[saved['bot']['user_id']]},tokens['ruslan'])['room_id']
                routes[room]=domain
            routes_path.write_text(json.dumps(routes,ensure_ascii=False,indent=2))
        lines=['Element: '+env['ELEMENT_PUBLIC_URL'],'Локальные пароли этой установки. Не добавлять в Git.','']
        for username,display in accounts:
            if username!='bot':lines.extend([display,'Логин: '+saved[username]['user_id'],'Пароль: '+saved[username]['password'],''])
        private_write(RUNTIME/'accounts.txt','\n'.join(lines))
    finally:
        for token in tokens.values():
            try:api(matrix,'POST','/_matrix/client/v3/logout',{},token)
            except Exception:pass
    compose('up','-d','--no-deps','bot')
    print('Element:',env['ELEMENT_PUBLIC_URL'])
    print('User and 10 staff accounts:',RUNTIME/'accounts.txt')

def start():
    env=settings()
    services = ('postgres','homeserver','element','litellm','backend','frontend')
    if env.get('OFFLINE_MODE') == '1':
        from offline_preflight import validate
        import yaml
        errors = validate(yaml.safe_load((ROOT/'config/litellm/offline.yaml').read_text()), env)
        if errors:
            raise RuntimeError('Offline preflight failed: ' + '; '.join(errors))
        compose('up','-d','--pull','never','--no-build',*services)
    else:
        if not env.get('NITEC_API_KEY'):
            raise RuntimeError('Fill NITEC_API_KEY in .env or configure OFFLINE_MODE=1; see docs/OFFLINE.md.')
        compose('up','-d','--build',*services)
        if env.get('MATRIX_E2EE_ENABLED')=='1':compose('build','bot')
    provision()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','start','provision'])
    args=parser.parse_args()
    {'init':initialize,'start':start,'provision':provision}[args.action]()
if __name__=='__main__':main()
