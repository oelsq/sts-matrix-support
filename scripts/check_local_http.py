"""Real local HTTP + Vite proxy smoke check; inference is explicitly a stub."""
import argparse,json,os,signal,shutil,socket,subprocess,sys,tempfile,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--frontend',type=Path,default=ROOT/'support-frontend',help='Frontend directory with installed npm dependencies')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
if not (args.frontend/'node_modules').is_dir():
 parser.error('Install frontend dependencies with npm ci first (or supply --frontend)')
workspace=tempfile.TemporaryDirectory(prefix='sts-http-smoke-')
OUT=Path(workspace.name)
def port():
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
def call(base,path,body=None,method=None):
 req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'},method=method)
 try:
  with urllib.request.urlopen(req,timeout=5) as res:return res.status,res.headers.get('content-type',''),res.read().decode()
 except urllib.error.HTTPError as res:return res.code,res.headers.get('content-type',''),res.read().decode()
backend,frontend=port(),port();processes=[];checks=[]
try:
 env={**os.environ,'DATABASE_URL':'sqlite:///'+str(OUT/'http-smoke.sqlite3'),'LLM_ENDPOINT':'','PYTHONPATH':str(ROOT/'matrix_support_bot')}
 processes.append(subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(backend)],cwd=ROOT,env=env,stdout=(OUT/'http-backend.log').open('w'),stderr=subprocess.STDOUT,start_new_session=True))
 env={**os.environ,'VITE_BACKEND_URL':f'http://127.0.0.1:{backend}'}
 processes.append(subprocess.Popen([shutil.which('npm') or 'npm','run','dev','--','--host','127.0.0.1','--port',str(frontend),'--strictPort'],cwd=args.frontend.resolve(),env=env,stdout=(OUT/'http-frontend.log').open('w'),stderr=subprocess.STDOUT,start_new_session=True))
 base=f'http://127.0.0.1:{frontend}'
 for _ in range(100):
  try:
   if call(base,'/api/health')[0]==200:break
  except (OSError,urllib.error.URLError):pass
  time.sleep(.1)
 else:raise RuntimeError('Local services did not become ready')
 def check(name,condition):
  checks.append({'name':name,'passed':bool(condition)})
  if not condition:raise AssertionError(name)
 status,ctype,text=call(base,'/');check('frontend HTML is served',status==200 and 'html' in ctype and '<div id="root"' in text)
 status,_,text=call(base,'/api/health');check('Vite /api prefix stripped and reaches API',status==200 and json.loads(text)=={'status':'ok'})
 status,_,text=call(base,'/api/tickets/',{'title':'Локальная проверка','description':'Synthetic data only'});ticket=json.loads(text);check('ticket created through proxy',status==200 and ticket['status']=='open');ident=ticket['id']
 status,_,text=call(base,f'/api/tickets/{ident}');check('ticket read through proxy',status==200 and json.loads(text)['title']=='Локальная проверка')
 status,_,text=call(base,f'/api/tickets/{ident}/messages/',{'content':'Проверка маршрута'});check('demo Echo is explicitly confirmed',status==200 and json.loads(text)['content']=='Echo: Проверка маршрута')
 status,_,text=call(base,f'/api/tickets/{ident}/status/',{'status':'pending'},'PUT');check('status updated',status==200 and json.loads(text)['status']=='pending')
 status,_,text=call(base,f'/api/tickets/{ident}');check('message history retained',status==200 and len(json.loads(text)['messages'])==2)
 status,_,text=call(base,'/api/tickets/');check('ticket appears in list',status==200 and any(t['id']==ident for t in json.loads(text)))
 check('negative pagination rejected',call(base,'/api/tickets/?limit=-1')[0]==422)
 check('unknown status rejected',call(base,f'/api/tickets/{ident}/status/',{'status':'invalid'},'PUT')[0]==422)
 check('blank message rejected',call(base,f'/api/tickets/{ident}/messages/',{'content':' '})[0]==422)
 check('missing ticket returns 404',call(base,'/api/tickets/999999')[0]==404)
finally:
 for process in processes:
  try:os.killpg(process.pid,signal.SIGTERM)
  except ProcessLookupError:pass
 for process in processes:
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL)
 args.output.write_text(json.dumps({'checks':checks,'actual_http':True,'inference':'ticket_demo_echo','real_model_tested':False,'production_nginx_tested':False},ensure_ascii=False,indent=2)+'\n')
workspace.cleanup()
print(json.dumps({'passed':sum(c['passed'] for c in checks),'total':len(checks)},ensure_ascii=False))
