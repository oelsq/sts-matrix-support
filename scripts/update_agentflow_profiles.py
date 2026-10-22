#!/usr/bin/env python3
"""Plan/apply/rollback instruction updates without changing routes, models or keys."""
import argparse
import json
import os
from pathlib import Path
import sys
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import Request,build_opener,HTTPCookieProcessor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'matrix_support_bot'))
sys.path.append('/app')
from flowise_profiles import load_profiles
from agentflow_upgrade import make_plan,apply_plan,rollback_plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default='/app/state')
    parser.add_argument('--plan-file',required=True)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--rollback',action='store_true')
    args=parser.parse_args();state=Path(args.state_dir);path=Path(args.plan_file)
    admin=json.loads((state/'flowise_admin.json').read_text())
    opener=build_opener(HTTPCookieProcessor(CookieJar()))
    def api(method,route,body=None):
        req=Request('http://flowise:3000/api/v1'+route,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json','x-request-from':'internal'},method=method)
        try:
            with opener.open(req,timeout=30) as res:
                data=res.read();return json.loads(data) if data else None
        except HTTPError as exc:raise RuntimeError('Flowise '+method+' '+route+' HTTP '+str(exc.code)) from None
    def save(plan):
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_name(path.name+'.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:json.dump(plan,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)
    api('POST','/auth/login',admin)
    routes=json.loads((state/'flowise_rooms.json').read_text())
    if args.apply or args.rollback:
        plan=json.loads(path.read_text())
        if plan.get('version')!=1:raise ValueError('Unknown plan version')
        (apply_plan if args.apply else rollback_plan)(plan,routes,api,save)
    else:
        if path.exists():raise ValueError('Plan already exists; choose another path')
        plan=make_plan(routes,api,load_profiles());save(plan)
    print('Agentflow instruction update:',plan['status'],'; flows:',len(plan['flows']))

if __name__=='__main__':main()
