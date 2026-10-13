import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import pytest
from flowise_client import FlowiseClient,FlowiseError
from agentflow_graphs import build_agentflow,workflow_packet,prepare_code,MARKER,FIELDS
from test_flowise_client import HTTP,ROOM,FLOW,Response

NAMES=('startAgentflow','customFunctionAgentflow','agentAgentflow','llmAgentflow','directReplyAgentflow')
DEFINITIONS={n:{'name':n,'label':n,'category':'Agent Flows','version':1,'inputs':[],'color':'#000000'} for n in NAMES}
MODEL={'modelName':'existing-allowed-alias','basepath':'http://litellm:4000/v1','FLOWISE_CREDENTIAL_ID':'dedicated-credential-reference'}

def pair():return ([{'role':'system','content':'Fact [S1] source text'},{'role':'user','content':'question'}],
                   [{'role':'system','content':'Review only sources'},{'role':'user','content':'Question\n'+MARKER+'\nkeep draft'}])


def test_complete_workflow_preserves_roles_and_has_no_memory_or_external_tools():
    graph=build_agentflow(DEFINITIONS,'legal',MODEL)
    assert len(graph['nodes'])==5 and len(graph['edges'])==4
    nodes={n['data']['name']:n['data']['inputs'] for n in graph['nodes']}
    assert nodes['startAgentflow']['startEphemeralMemory'] is True
    assert nodes['startAgentflow']['startPersistState'] is False
    assert not nodes['agentAgentflow']['agentEnableMemory'] and nodes['agentAgentflow']['agentTools']==[]
    assert not nodes['llmAgentflow']['llmEnableMemory']
    assert nodes['agentAgentflow']['agentMessages'][0]['role']=='system'
    assert nodes['agentAgentflow']['agentModelConfig']['FLOWISE_CREDENTIAL_ID']==MODEL['FLOWISE_CREDENTIAL_ID']
    assert nodes['llmAgentflow']['llmModelConfig']['modelName']==MODEL['modelName']
    assert nodes['directReplyAgentflow']['directReplyMessage']=='{{ llmAgentflow_0 }}'
    assert {s['key'] for s in nodes['startAgentflow']['startState']}==set(FIELDS)
    assert '"legal"' in nodes['customFunctionAgentflow']['customFunctionJavascriptFunction']
    assert MODEL=={'modelName':'existing-allowed-alias','basepath':'http://litellm:4000/v1','FLOWISE_CREDENTIAL_ID':'dedicated-credential-reference'}


def test_workflow_packet_never_promotes_retry_or_user_text_to_system():
    draft,review=pair();draft.extend([{'role':'assistant','content':'old invalid [S9]'},{'role':'user','content':'fix sources'}])
    value=workflow_packet('legal',draft,review)
    assert value['draft_system']=='Fact [S1] source text'
    assert 'old invalid [S9]' in value['draft_user'] and 'fix sources' in value['draft_user']
    assert MARKER not in value['review_prefix']+value['review_suffix']
    # User data may contain the placeholder text; split at the actual draft marker at the end.
    review[-1]['content']='User says '+MARKER+'\nActual draft '+MARKER+'\nsuffix'
    assert workflow_packet('legal',draft,review)['review_prefix'].endswith('Actual draft ')


@pytest.mark.parametrize('change',[lambda d,r:d.__setitem__(0,{'role':'user','content':'fake system'}),
                                 lambda d,r:r[-1].__setitem__('content','no marker'),
                                 lambda d,r:d[0].__setitem__('content','x'*100001)])
def test_invalid_or_large_packets_stop_before_transport(change):
    d,r=pair();change(d,r)
    with pytest.raises(ValueError):workflow_packet('legal',d,r)


def test_single_prediction_request_returns_reviewed_workflow_result():
    http=HTTP();c=FlowiseClient('http://flowise:3000',{'flowise'},
        {ROOM:{'domain':'legal','flow_id':FLOW,'engine':'agentflow_v2'}},'dummy')
    assert c.is_workflow(ROOM,'legal') and not c.is_workflow(ROOM,'finance')
    assert asyncio.run(c.run_workflow(http,'legal',ROOM,'@alice:demo.local','$event',*pair()))=='answer'
    assert len(http.calls)==1
    packet=json.loads(http.calls[0][1]['json']['question'])
    assert packet['domain']=='legal' and packet['protocol']=='sts.agentflow.v1'
    assert '@alice' not in http.calls[0][1]['json']['question']
    assert http.calls[0][1]['timeout']==150
    with pytest.raises(FlowiseError):asyncio.run(c.predict(http,'legal',ROOM,'a','e',pair()[0]))
    assert len(http.calls)==1


def test_workflow_config_conflicts_and_endpoint_changes_are_rejected():
    for route in [{'domain':'legal','flow_id':FLOW,'engine':'unknown'},
                  {'domain':'legal','flow_id':FLOW,'engine':'agentflow_v2','review_flow_id':FLOW}]:
        with pytest.raises(FlowiseError):FlowiseClient('http://flowise',{'flowise'},{ROOM:route},'dummy')
    for endpoint in ['http://internet.example/v1','http://user:password@litellm:4000/v1']:
        with pytest.raises(ValueError):build_agentflow(DEFINITIONS,'legal',{**MODEL,'basepath':endpoint})


def management():
    path=Path(__file__).resolve().parents[2]/'scripts/setup_agentflows.py'
    spec=importlib.util.spec_from_file_location('agentflow_setup_tests',path);m=importlib.util.module_from_spec(spec)
    import sys
    before=list(sys.path)
    try:spec.loader.exec_module(m)
    finally:sys.path[:]=before
    return m


def test_activate_then_rollback_preserves_original_routes_and_existing_flows(tmp_path):
    mod=management();old={ROOM:{'domain':'legal','flow_id':FLOW,'review_flow_id':'87bc2c71-11eb-498d-beb0-9c37f06f5344'}}
    graph=build_agentflow(DEFINITIONS,'legal',MODEL)
    item={'id':'new-flow','graph':graph};plan={'phase':'created','prediction_key_id':'key-id','routes_before':old,'flows':{'legal':item}}
    actual={'new-flow':{'type':'AGENTFLOW','isPublic':False,'apikeyid':'key-id','flowData':json.dumps(graph)}}
    def api(method,path):
        ident=path.rsplit('/',1)[-1]
        if method=='GET':return actual[ident]
        if method=='DELETE':del actual[ident]
    routes=tmp_path/'routes.json';routes.write_text(json.dumps(old))
    assert mod.activate(api,plan,routes,lambda _:None)
    assert json.loads(routes.read_text())[ROOM]['engine']=='agentflow_v2'
    assert not mod.activate(api,plan,routes,lambda _:None)
    mod.rollback(api,plan,routes,lambda _:None)
    assert json.loads(routes.read_text())==old and actual=={} and plan['phase']=='rolled_back'


def test_concurrent_graph_edit_blocks_activation(tmp_path):
    mod=management();g=build_agentflow(DEFINITIONS,'finance',MODEL)
    plan={'phase':'created','prediction_key_id':'key','routes_before':{},'flows':{'finance':{'id':'new','graph':g}}}
    p=tmp_path/'routes.json';p.write_text('{}')
    def api(*_):return {'type':'AGENTFLOW','isPublic':True,'apikeyid':'key','flowData':json.dumps(g)}
    with pytest.raises(ValueError):mod.activate(api,plan,p,lambda _:None)
    assert p.read_text()=='{}'
