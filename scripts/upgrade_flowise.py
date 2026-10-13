#!/usr/bin/env python3
"""Plan/apply/rollback two Flowise stages. Run inside bot; no model key is read or copied."""
import argparse
import hashlib
import json
import os
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
sys.path.append('/app')
from flowise_profiles import load_profiles, stage_graph

FIELDS = ('name', 'flowData', 'type', 'deployed', 'isPublic', 'apikeyid')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def payload(flow):
    return {k: flow.get(k) for k in FIELDS}


def private_json(path, value, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    temporary = path.with_name(path.name + '.tmp')
    if exclusive and path.exists():
        raise ValueError('Plan exists; choose another path')
    fd = os.open(temporary, flags, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def apply_plan(plan, api, state, save):
    if plan['status'] != 'planned':
        raise ValueError('Plan is not pending')
    for name, original in plan['state_before'].items():
        if json.loads((state/name).read_text()) != original:
            raise ValueError('Flowise state changed after planning')
    for item in plan['domains'].values():
        if digest(payload(api('GET', '/chatflows/'+item['id']))) != digest(item['before']):
            raise ValueError('Flow changed after planning; make a new plan')
    plan['status'] = 'applying'; save(plan)
    # Record every created object before continuing, so interrupted upgrades can be rolled back.
    for domain, item in plan['domains'].items():
        review = dict(item['after'], name='STS Flowise — '+('ЮрАгент' if domain == 'legal' else 'ФинАгент')+' — проверка',
                      flowData=json.dumps(item['review_graph'], ensure_ascii=False))
        created = api('POST', '/chatflows', review)
        item['review_id'] = created['id']; save(plan)
        api('PUT', '/chatflows/'+item['id'], item['after'])
        item['draft_updated'] = True; save(plan)
    setup = dict(plan['state_before']['flowise_setup.json'])
    setup['review_flows'] = {d:v['review_id'] for d,v in plan['domains'].items()}
    routes = json.loads(json.dumps(plan['state_before']['flowise_rooms.json']))
    for entry in routes.values():
        item = plan['domains'][entry['domain']]
        if entry['flow_id'] != item['id']:
            raise ValueError('Room uses an unplanned flow')
        entry['review_flow_id'] = item['review_id']
    plan['state_after'] = {'flowise_setup.json':setup,'flowise_rooms.json':routes}; save(plan)
    for name,value in plan['state_after'].items():private_json(state/name,value)
    plan['status'] = 'applied'; save(plan)


def rollback_plan(plan, api, state, save):
    if plan['status'] not in {'applying', 'applied', 'rolling_back'}:
        raise ValueError('Plan has no applied changes')
    # Refuse to overwrite independent administrator edits. A partial apply may still have originals.
    for name,original in plan['state_before'].items():
        current = json.loads((state/name).read_text())
        if current != original and current != plan.get('state_after',{}).get(name):
            raise ValueError('State changed independently; rollback refused')
    for item in plan['domains'].values():
        current = payload(api('GET','/chatflows/'+item['id']))
        if digest(current) not in {digest(item['before']),digest(item['after'])}:
            raise ValueError('Flow changed independently; rollback refused')
        if item.get('review_id'):
            review=payload(api('GET','/chatflows/'+item['review_id']))
            if json.loads(review['flowData']) != item['review_graph']:
                raise ValueError('Review flow changed independently; rollback refused')
    plan['status']='rolling_back';save(plan)
    for name,original in plan['state_before'].items():private_json(state/name,original)
    for item in plan['domains'].values():
        api('PUT','/chatflows/'+item['id'],item['before'])
        if item.get('review_id'):
            api('DELETE','/chatflows/'+item['review_id'])
            item.pop('review_id');save(plan)
    plan['status']='rolled_back';save(plan)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default='/app/state')
    parser.add_argument('--profiles')
    parser.add_argument('--plan-file',required=True)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true');mode.add_argument('--rollback',action='store_true')
    args=parser.parse_args();state=Path(args.state_dir);plan_path=Path(args.plan_file)
    admin=json.loads((state/'flowise_admin.json').read_text())
    opener=build_opener(HTTPCookieProcessor(CookieJar()))
    def api(method,path,body=None):
        request=Request('http://flowise:3000/api/v1'+path,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json','x-request-from':'internal'},method=method)
        try:
            with opener.open(request,timeout=30) as response:
                data=response.read();return json.loads(data) if data else None
        except HTTPError as error:
            raise RuntimeError('Flowise '+method+' '+path+': HTTP '+str(error.code)) from None
    api('POST','/auth/login',admin)
    save=lambda plan:private_json(plan_path,plan)
    if args.apply or args.rollback:
        plan=json.loads(plan_path.read_text())
        if args.apply:apply_plan(plan,api,state,save)
        else:rollback_plan(plan,api,state,save)
        print('Flowise stages:',plan['status'],'(restart only the bot to reload routes)');return
    profiles=load_profiles(args.profiles)
    setup=json.loads((state/'flowise_setup.json').read_text())
    routes=json.loads((state/'flowise_rooms.json').read_text())
    if setup.get('review_flows') or any('review_flow_id' in r for r in routes.values()):
        raise ValueError('Stage flows already configured; edit them in Flowise instead of replacing them')
    plan={'version':1,'status':'planned','state_before':{'flowise_setup.json':setup,'flowise_rooms.json':routes},'domains':{}}
    for domain in ('legal','finance'):
        flow=api('GET','/chatflows/'+setup['flows'][domain]);before=payload(flow)
        if before['isPublic'] or before['apikeyid'] != setup['prediction_key_id']:
            raise ValueError('Flow must use the existing protected prediction API key')
        graph=json.loads(flow['flowData'])
        after=dict(before,flowData=json.dumps(stage_graph(graph,profiles['domains'][domain]['draft']),ensure_ascii=False))
        plan['domains'][domain]={'id':flow['id'],'before':before,'after':after,
                                'review_graph':stage_graph(graph,profiles['domains'][domain]['review'])}
    for entry in routes.values():
        if entry['flow_id'] != plan['domains'][entry['domain']]['id']:
            raise ValueError('Room uses an unplanned flow')
    private_json(plan_path,plan,exclusive=True)
    print('Planned two draft updates and two protected review flows. No remote changes yet.')

if __name__=='__main__':main()
