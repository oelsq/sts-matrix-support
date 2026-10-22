"""Stage and activate protected quality graphs, keeping previous flows for rollback."""
import copy,json
from agentflow_upgrade import payload,digest,update_graph
from agentflow_quality_graph import build_quality_agentflow
from flowise_profiles import load_profiles

NAMES=('startAgentflow','customFunctionAgentflow','agentAgentflow','llmAgentflow','directReplyAgentflow','conditionAgentflow')


def plan_release(routes,api):
    grouped={}
    for room,entry in routes.items():
        if entry.get('engine')!='agentflow_v2' or entry.get('quality_contract'):
            raise ValueError('Expected original Agentflow V2 routes')
        grouped.setdefault(entry['domain'],set()).add(entry['flow_id'])
    if set(grouped)!={'legal','finance'} or any(len(ids)!=1 for ids in grouped.values()):
        raise ValueError('Expected one existing flow per domain')
    definitions={n:api('GET','/nodes/'+n) for n in NAMES}
    plan={'version':1,'phase':'planned','routes_before':copy.deepcopy(routes),'flows':{}}
    for domain,ids in grouped.items():
        ident=next(iter(ids));old=payload(api('GET','/chatflows/'+ident))
        if old['type']!='AGENTFLOW' or old['isPublic'] or not old['apikeyid']:raise ValueError('Expected protected original flow')
        graph=json.loads(old['flowData']);update_graph(graph,domain,load_profiles()) # Validate known stateless topology.
        models=[n['data']['inputs']['agentModelConfig'] for n in graph['nodes'] if n['data']['name']=='agentAgentflow']
        new=build_quality_agentflow(definitions,domain,models[0])
        plan['flows'][domain]={'original_id':ident,'original':old,'graph':new}
    return plan


def create_release(plan,api,save):
    if plan['phase'] not in {'planned','creating'}:raise ValueError('Release already created')
    plan['phase']='creating';save(plan)
    for domain,item in plan['flows'].items():
        if item.get('candidate_id'):continue
        created=api('POST','/chatflows',{'name':'STS Agentflow V2 — '+('ЮрАгент' if domain=='legal' else 'ФинАгент')+' — проверка утверждений',
            'flowData':json.dumps(item['graph'],ensure_ascii=False),'type':'AGENTFLOW','deployed':True,'isPublic':False,'apikeyid':item['original']['apikeyid']})
        item['candidate_id']=created['id'];save(plan)
    verify_release(plan,api);plan['phase']='created';save(plan)


def verify_release(plan,api):
    for item in plan['flows'].values():
        current=api('GET','/chatflows/'+item['candidate_id'])
        if current.get('type')!='AGENTFLOW' or current.get('isPublic') or current.get('apikeyid')!=item['original']['apikeyid'] or digest(json.loads(current['flowData']))!=digest(item['graph']):
            raise ValueError('Candidate graph changed independently')


def release_routes(plan):
    return {room:dict(entry,flow_id=plan['flows'][entry['domain']]['candidate_id'],quality_contract='sts.claims.v1') for room,entry in plan['routes_before'].items()}


def activate_release(plan,routes,api,save,save_routes):
    if plan['phase']!='created':raise ValueError('Create and test candidates first')
    if routes!=plan['routes_before']:raise ValueError('Routes changed independently')
    verify_release(plan,api)
    for item in plan['flows'].values():
        if digest(payload(api('GET','/chatflows/'+item['original_id'])))!=digest(item['original']):raise ValueError('Original flow changed independently')
    after=release_routes(plan);plan['routes_after']=after;save(plan)
    save_routes(after);plan['phase']='active';save(plan)


def rollback_release(plan,routes,save,save_routes):
    if plan['phase']!='active' or routes!=plan['routes_after']:raise ValueError('Release routes changed; rollback refused')
    save_routes(plan['routes_before']);plan['phase']='rolled_back';save(plan)
