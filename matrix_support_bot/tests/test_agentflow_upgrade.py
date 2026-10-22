import copy,json
import pytest
from agentflow_graphs import build_agentflow
from agentflow_upgrade import NAMES,update_graph,make_plan,apply_plan,rollback_plan
from flowise_profiles import load_profiles

DEFS={n:{'name':n,'category':'Agent Flows','version':1,'inputs':[],'color':'#000'} for n in NAMES}
MODEL={'modelName':'existing-model','basepath':'http://litellm:4000/v1','FLOWISE_CREDENTIAL_ID':'existing-credential'}


def setup():
    profiles=load_profiles()
    flows={d:{'name':d,'flowData':json.dumps(build_agentflow(DEFS,d,MODEL)),'type':'AGENTFLOW','deployed':True,'isPublic':False,'apikeyid':'existing-key-id'} for d in ['legal','finance']}
    routes={'!'+d+':local':{'domain':d,'flow_id':d,'engine':'agentflow_v2'} for d in flows}
    for d in profiles['domains']:
        profiles['domains'][d]['draft']['instructions']='NEW DRAFT '+d
        profiles['domains'][d]['review']['instructions']='NEW REVIEW '+d
    writes=[]
    def api(method,path,body=None):
        ident=path.rsplit('/',1)[-1]
        if method=='GET':return copy.deepcopy(flows[ident])
        assert method=='PUT';writes.append(ident);flows[ident]=copy.deepcopy(body)
        return copy.deepcopy(body)
    return profiles,flows,routes,writes,api


def test_only_instructions_and_generation_limits_change():
    profiles,flows,_,_,_=setup()
    before=json.loads(flows['legal']['flowData'])
    after=update_graph(before,'legal',profiles)
    assert after['edges']==before['edges']
    assert before!=after
    for original,updated in zip(before['nodes'],after['nodes']):
        kind=original['data']['name']
        if kind not in {'agentAgentflow','llmAgentflow'}:assert updated==original;continue
        prefix='agent' if kind=='agentAgentflow' else 'llm'
        old=original['data']['inputs'];new=updated['data']['inputs']
        for key in ['modelName','basepath','FLOWISE_CREDENTIAL_ID']:
            assert new[prefix+'ModelConfig'][key]==old[prefix+'ModelConfig'][key]
        assert new[prefix+'Messages'][1]==old[prefix+'Messages'][1]
        assert new[prefix+'Messages'][0]['content'].startswith('NEW ')
        assert new[prefix+'EnableMemory'] is False


def test_apply_and_rollback_do_not_change_ids_keys_or_room_routes():
    profiles,flows,routes,writes,api=setup();before=copy.deepcopy(flows);original_routes=copy.deepcopy(routes)
    plan=make_plan(routes,api,profiles)
    assert not writes
    apply_plan(plan,routes,api,lambda _:None)
    assert plan['status']=='applied' and set(writes)=={'legal','finance'}
    assert routes==original_routes
    for d,f in flows.items():assert f['apikeyid']==before[d]['apikeyid']
    rollback_plan(plan,routes,api,lambda _:None)
    assert flows==before and routes==original_routes


def test_independent_graph_edit_prevents_any_apply():
    profiles,flows,routes,writes,api=setup();plan=make_plan(routes,api,profiles)
    flows['finance']['name']='manually edited'
    with pytest.raises(ValueError,match='independently'):apply_plan(plan,routes,api,lambda _:None)
    assert not writes


@pytest.mark.parametrize('change',[
    lambda g:g['nodes'][2]['data']['inputs'].__setitem__('agentEnableMemory',True),
    lambda g:g['nodes'][2]['data']['inputs'].__setitem__('agentTools',['tool']),
    lambda g:g['edges'][0].__setitem__('target','directReplyAgentflow_0'),
    lambda g:g['nodes'][2]['data']['inputs']['agentMessages'][0].__setitem__('content','unknown state binding'),
])
def test_unknown_or_stateful_workflow_is_not_silently_rewritten(change):
    profiles,flows,_,_,_=setup();graph=json.loads(flows['legal']['flowData']);change(graph)
    with pytest.raises(ValueError):update_graph(graph,'legal',profiles)


def test_interrupted_second_update_can_be_rolled_back():
    profiles,flows,routes,writes,api=setup();before=copy.deepcopy(flows)
    plan=make_plan(routes,api,profiles)
    def failing(method,path,body=None):
        if method=='PUT' and path.endswith('/finance'):raise RuntimeError('simulated transport failure')
        return api(method,path,body)
    with pytest.raises(RuntimeError):apply_plan(plan,routes,failing,lambda _:None)
    assert plan['status']=='applying'
    rollback_plan(plan,routes,api,lambda _:None)
    assert flows==before
