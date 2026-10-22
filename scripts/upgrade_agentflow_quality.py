#!/usr/bin/env python3
"""Plan/create/test/activate claim-audited Agentflows V2; keep original graphs."""
import argparse,json,os,sys
from pathlib import Path
from http.cookiejar import CookieJar
from urllib.request import Request,build_opener,HTTPCookieProcessor
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'matrix_support_bot'))
sys.path.append('/app')
from agentflow_release import plan_release,create_release,activate_release,rollback_release
from agentflow_quality_refresh import refresh_plan
from agentflow_upgrade import apply_plan, rollback_plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default='/app/handoff_state');parser.add_argument('--plan-file',required=True)
    parser.add_argument('--refresh',action='store_true',help='Update an existing 13-node claim workflow in place; preserve IDs/models')
    modes=parser.add_mutually_exclusive_group()
    for flag in ['create','activate','rollback']:modes.add_argument('--'+flag,action='store_true')
    args=parser.parse_args();state=Path(args.state_dir);path=Path(args.plan_file)
    opener=build_opener(HTTPCookieProcessor(CookieJar()))
    def api(method,route,body=None):
        req=Request('http://flowise:3000/api/v1'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','x-request-from':'internal'},method=method)
        try:
            with opener.open(req,timeout=40) as r:
                data=r.read();return json.loads(data) if data else None
        except HTTPError as exc:raise RuntimeError('Flowise HTTP '+str(exc.code)) from None
    def write(dest,value):
        dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_name(dest.name+'.tmp')
        fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2)
        os.replace(tmp,dest)
    api('POST','/auth/login',json.loads((state/'flowise_admin.json').read_text()))
    routes=json.loads((state/'flowise_rooms.json').read_text());save=lambda plan:write(path,plan)
    if not any([args.create,args.activate,args.rollback]):
        if path.exists():raise ValueError('Choose a new plan path')
        plan=(refresh_plan(routes,api) if args.refresh else plan_release(routes,api));save(plan)
    else:
        plan=json.loads(path.read_text())
        if plan.get('version')!=1:raise ValueError('Unknown plan')
        if args.refresh:
            if args.create:raise ValueError('Refresh uses plan, --activate or --rollback; no new flows')
            if args.activate:apply_plan(plan,routes,api,save)
            else:rollback_plan(plan,routes,api,save)
        elif args.create:create_release(plan,api,save)
        elif args.activate:activate_release(plan,routes,api,save,lambda v:write(state/'flowise_rooms.json',v))
        else:rollback_release(plan,routes,save,lambda v:write(state/'flowise_rooms.json',v))
    print('Claim-audited Agentflows:',plan.get('phase',plan.get('status')),'; flows:',len(plan['flows']))
if __name__=='__main__':main()
