"""Update only the two instruction profiles in known stateless Agentflows V2."""
import copy
import hashlib
import json
from agentflow_graphs import prepare_code

FIELDS = ('name', 'flowData', 'type', 'deployed', 'isPublic', 'apikeyid')
NAMES = ('startAgentflow', 'customFunctionAgentflow', 'agentAgentflow', 'llmAgentflow', 'directReplyAgentflow')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def payload(flow):
    return {key:flow.get(key) for key in FIELDS}


def update_graph(graph, domain, profiles):
    result=copy.deepcopy(graph)
    nodes={n['data']['name']:n for n in result['nodes']}
    if len(result['nodes'])!=5 or set(nodes)!=set(NAMES):
        raise ValueError('Expected known five-node Agentflow V2')
    expected={(a+'_0',b+'_0') for a,b in zip(NAMES,NAMES[1:])}
    if {(e['source'],e['target']) for e in result['edges']}!=expected:
        raise ValueError('Unexpected workflow wiring')
    start=nodes['startAgentflow']['data']['inputs']
    if not start.get('startEphemeralMemory') or start.get('startPersistState'):
        raise ValueError('Workflow must remain ephemeral')
    if nodes['customFunctionAgentflow']['data']['inputs']['customFunctionJavascriptFunction']!=prepare_code(domain):
        raise ValueError('Unknown packet preparation function')
    for node,kind,stage,state in [('agentAgentflow','agent','draft','draft_system'),('llmAgentflow','llm','review','review_system')]:
        values=nodes[node]['data']['inputs']
        if values.get(kind+'EnableMemory') or (kind=='agent' and values.get('agentTools')):
            raise ValueError('Stateful or tool-enabled workflow requires separate review')
        messages=values[kind+'Messages']
        suffix='\n{{ $flow.state.'+state+' }}'
        if len(messages)!=2 or [m['role'] for m in messages]!=['system','user'] or not messages[0]['content'].endswith(suffix):
            raise ValueError('Unknown role/state template; refusing to replace it')
        profile=profiles['domains'][domain][stage]
        messages[0]['content']=profile['instructions']+suffix
        model=values[kind+'ModelConfig']
        model['temperature']=profile['temperature']
        model['maxTokens']=profile['max_tokens']
    return result


def make_plan(routes, api, profiles):
    grouped={}
    for route in routes.values():
        if route.get('engine')!='agentflow_v2' or route['domain'] not in {'legal','finance'}:
            raise ValueError('Only existing Agentflows V2 routes may be updated')
        grouped.setdefault(route['domain'],set()).add(route['flow_id'])
    if set(grouped)!={'legal','finance'} or any(len(ids)!=1 for ids in grouped.values()):
        raise ValueError('Expected one existing flow per specialist')
    plan={'version':1,'status':'planned','routes_digest':digest(routes),'flows':{}}
    for domain,ids in grouped.items():
        ident=next(iter(ids));before=payload(api('GET','/chatflows/'+ident))
        if before['type']!='AGENTFLOW' or before['isPublic'] or not before['apikeyid']:
            raise ValueError('Expected a protected Agentflow')
        graph=update_graph(json.loads(before['flowData']),domain,profiles)
        after=dict(before,flowData=json.dumps(graph,ensure_ascii=False))
        plan['flows'][domain]={'id':ident,'before':before,'after':after}
    return plan


def apply_plan(plan, routes, api, save):
    if plan['status']!='planned' or digest(routes)!=plan['routes_digest']:
        raise ValueError('Plan or routes changed')
    for item in plan['flows'].values():
        if digest(payload(api('GET','/chatflows/'+item['id'])))!=digest(item['before']):
            raise ValueError('Flow changed independently; replan before applying')
    plan['status']='applying';save(plan)
    for item in plan['flows'].values():
        api('PUT','/chatflows/'+item['id'],item['after'])
        item['updated']=True;save(plan)
    plan['status']='applied';save(plan)


def rollback_plan(plan, routes, api, save):
    if plan['status'] not in {'applying','applied'} or digest(routes)!=plan['routes_digest']:
        raise ValueError('Cannot roll back this plan/routes')
    for item in plan['flows'].values():
        if digest(payload(api('GET','/chatflows/'+item['id']))) not in {digest(item['before']),digest(item['after'])}:
            raise ValueError('Flow changed independently; rollback refused')
    for item in plan['flows'].values():
        api('PUT','/chatflows/'+item['id'],item['before'])
    plan['status']='rolled_back';save(plan)
