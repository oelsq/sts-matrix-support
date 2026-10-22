import copy
import json
import pytest
from agentflow_quality_graph import build_quality_agentflow
from agentflow_quality_refresh import refresh_plan
from agentflow_upgrade import apply_plan, rollback_plan


def fixtures():
    names=['startAgentflow','customFunctionAgentflow','agentAgentflow','llmAgentflow','directReplyAgentflow','conditionAgentflow']
    definitions={name:{'name':name,'category':'Agent Flows','inputs':[]} for name in names}
    flows={}
    for domain in ['legal','finance']:
        graph=build_quality_agentflow(definitions,domain,{'modelName':'private-model','basepath':'http://litellm:4000/v1','FLOWISE_CREDENTIAL_ID':'reference'})
        graph['nodes'][0]['position']['y']=555
        graph['nodes'][4]['data']['inputs']['llmModelConfig']['modelName']='separate-review-model'
        graph['nodes'][5]['data']['inputs']['customFunctionJavascriptFunction']='old gate'
        flows[domain]={'name':domain,'type':'AGENTFLOW','flowData':json.dumps(graph),'isPublic':False,'apikeyid':'existing-protection','deployed':True}
    routes={d:{'domain':d,'engine':'agentflow_v2','quality_contract':'sts.claims.v1','flow_id':d} for d in flows}
    def api(method,path,body=None):
        if path.startswith('/nodes/'):return copy.deepcopy(definitions[path.split('/')[-1]])
        ident=path.split('/')[-1]
        if method=='PUT':flows[ident]=copy.deepcopy(body)
        return copy.deepcopy(flows[ident])
    return routes,flows,api


def test_quality_refresh_keeps_ids_layout_protection_and_per_stage_models_and_can_roll_back():
    routes,flows,api=fixtures();original=copy.deepcopy(flows)
    plan=refresh_plan(routes,api);apply_plan(plan,routes,api,lambda _:None)
    for domain in flows:
        after=json.loads(flows[domain]['flowData']);before=json.loads(original[domain]['flowData'])
        assert after['nodes'][0]['position']==before['nodes'][0]['position']
        assert [n['id'] for n in after['nodes']]==[n['id'] for n in before['nodes']]
        for a,b in zip(after['nodes'],before['nodes']):
            for key in ['agentModelConfig','llmModelConfig']:assert a['data']['inputs'].get(key)==b['data']['inputs'].get(key)
        assert 'numericRelationErrors' in after['nodes'][5]['data']['inputs']['customFunctionJavascriptFunction']
        assert flows[domain]['apikeyid']==original[domain]['apikeyid']
    rollback_plan(plan,routes,api,lambda _:None);assert flows==original

@pytest.mark.parametrize('change',['topology','public','memory','concurrent'])
def test_quality_refresh_refuses_unknown_or_independently_changed_workflows(change):
    routes,flows,api=fixtures()
    if change=='concurrent':
        plan=refresh_plan(routes,api);flows['legal']['name']='Changed by operator'
        with pytest.raises(ValueError):apply_plan(plan,routes,api,lambda _:None)
        return
    if change=='public':flows['legal']['isPublic']=True
    else:
        graph=json.loads(flows['legal']['flowData'])
        if change=='topology':graph['edges'].pop()
        else:graph['nodes'][2]['data']['inputs']['agentEnableMemory']=True
        flows['legal']['flowData']=json.dumps(graph)
    with pytest.raises(ValueError):refresh_plan(routes,api)
