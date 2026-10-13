#!/usr/bin/env python3
"""Stage protected Agentflows V2, test before activating, keep existing Chatflows for rollback."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import Request,build_opener,HTTPCookieProcessor
sys.path.append('/app')
from agentflow_graphs import build_agentflow

NODES=('startAgentflow','customFunctionAgentflow','agentAgentflow','llmAgentflow','directReplyAgentflow')


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    os.replace(tmp,path)


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate_created(api,plan):
    for item in plan['flows'].values():
        flow=api('GET','/chatflows/'+item['id'])
        if (flow.get('type')!='AGENTFLOW' or flow.get('isPublic') or flow.get('apikeyid')!=plan['prediction_key_id']
                or fingerprint(json.loads(flow['flowData']))!=fingerprint(item['graph'])):
            raise ValueError('Created Agentflow was modified independently')


def activate(api,plan,routes_path,save_plan):
    if plan['phase'] not in {'created','active'}:raise ValueError('Create and test both flows first')
    validate_created(api,plan)
    after={r:{'domain':v['domain'],'flow_id':plan['flows'][v['domain']]['id'],'engine':'agentflow_v2'} for r,v in plan['routes_before'].items()}
    current=json.loads(routes_path.read_text())
    if current==after:return False
    if current!=plan['routes_before']:raise ValueError('Routes changed independently; activation refused')
    plan['routes_after']=after;save_plan(plan)
    save(routes_path,after);plan['phase']='active';save_plan(plan);return True


def rollback(api,plan,routes_path,save_plan):
    if plan['phase'] not in {'creating','created','active','rolling_back'}:raise ValueError('Nothing to roll back')
    current=json.loads(routes_path.read_text())
    if current not in [plan['routes_before'],plan.get('routes_after')]:raise ValueError('Routes changed independently; rollback refused')
    created={d:v for d,v in plan['flows'].items() if v.get('id')}
    validate_created(api,{**plan,'flows':created})
    plan['phase']='rolling_back';save_plan(plan);save(routes_path,plan['routes_before'])
    for item in created.values():
        api('DELETE','/chatflows/'+item['id']);del item['id'];save_plan(plan)
    plan['phase']='rolled_back';save_plan(plan)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default='/app/state');parser.add_argument('--plan-file',required=True)
    modes=parser.add_mutually_exclusive_group();modes.add_argument('--create',action='store_true');modes.add_argument('--activate',action='store_true');modes.add_argument('--rollback',action='store_true')
    args=parser.parse_args();state=Path(args.state_dir);path=Path(args.plan_file);routes=state/'flowise_rooms.json'
    admin=json.loads((state/'flowise_admin.json').read_text());opener=build_opener(HTTPCookieProcessor(CookieJar()))
    def api(method,route,body=None):
        req=Request('http://flowise:3000/api/v1'+route,data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json','x-request-from':'internal'},method=method)
        try:
            with opener.open(req,timeout=45) as response:
                data=response.read();return json.loads(data) if data else None
        except HTTPError as error:raise RuntimeError('Flowise '+method+' '+route+' HTTP '+str(error.code)) from None
    api('POST','/auth/login',admin)
    persist=lambda value:save(path,value)
    if args.create or args.activate or args.rollback:
        plan=json.loads(path.read_text())
        if plan.get('version')!=1:raise ValueError('Unknown plan version')
        if args.activate:
            activate(api,plan,routes,persist);print('Agentflows V2 routes active; restart only bot');return
        if args.rollback:
            rollback(api,plan,routes,persist);print('Agentflow routes and created flows rolled back; restart only bot');return
        if plan['phase'] not in {'planned','creating'}:raise ValueError('Plan already created')
        plan['phase']='creating';persist(plan)
        for domain,item in plan['flows'].items():
            if item.get('id'):continue
            flow=api('POST','/chatflows',{'name':'STS Agentflow V2 — '+('ЮрАгент' if domain=='legal' else 'ФинАгент'),
                  'flowData':json.dumps(item['graph'],ensure_ascii=False),'type':'AGENTFLOW','deployed':True,'isPublic':False,
                  'apikeyid':plan['prediction_key_id']})
            item['id']=flow['id'];persist(plan)
        validate_created(api,plan);plan['phase']='created';persist(plan)
        print('Two protected Agentflows V2 created; existing routes unchanged');return
    if path.exists():raise ValueError('Plan already exists; choose another path')
    setup=json.loads((state/'flowise_setup.json').read_text());existing=json.loads(routes.read_text())
    if not existing or any(v.get('engine')=='agentflow_v2' for v in existing.values()):raise ValueError('Expected existing Chatflow room routes')
    definitions={n:api('GET','/nodes/'+n) for n in NODES}
    plan={'version':1,'phase':'planned','prediction_key_id':setup['prediction_key_id'],'routes_before':existing,'flows':{}}
    for domain in ('legal','finance'):
        source=api('GET','/chatflows/'+setup['flows'][domain]);graph=json.loads(source['flowData'])
        models=[n['data'] for n in graph['nodes'] if n['data']['name']=='chatOpenAI']
        if len(models)!=1:raise ValueError('Expected one existing model configuration')
        model=copy.deepcopy(models[0]['inputs']);model['FLOWISE_CREDENTIAL_ID']=models[0]['credential']
        plan['flows'][domain]={'graph':build_agentflow(definitions,domain,model)}
    for entry in existing.values():
        if entry['domain'] not in plan['flows']:raise ValueError('Unknown room domain')
    persist(plan);print('Agentflows V2 plan prepared; no remote changes')

if __name__=='__main__':main()
