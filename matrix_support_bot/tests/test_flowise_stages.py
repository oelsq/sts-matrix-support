"""Separate draft/review routing, preserved graph credentials, and reversible provisioning."""
import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path
import pytest
from flowise_client import FlowiseClient, FlowiseError
from flowise_profiles import load_profiles, stage_graph
from test_flowise_client import HTTP, ROOM, FLOW

REVIEW='87bc2c71-11eb-498d-beb0-9c37f06f5344'


def test_review_route_is_explicit_and_stage_session_is_isolated():
    http=HTTP()
    c=FlowiseClient('http://flowise:3000',{'flowise'},
        {ROOM:{'domain':'legal','flow_id':FLOW,'review_flow_id':REVIEW}},'fake')
    for stage in ('draft','review'):
        asyncio.run(c.predict(http,'legal',ROOM,'user','event',[{'role':'user','content':'[S1]'}],stage=stage))
    assert http.calls[0][0].endswith(FLOW)
    assert http.calls[1][0].endswith(REVIEW)
    assert http.calls[0][1]['json']['overrideConfig'] != http.calls[1][1]['json']['overrideConfig']
    with pytest.raises(FlowiseError):
        asyncio.run(c.predict(http,'legal',ROOM,'u','e',[],stage='other'))
    assert len(http.calls)==2


def test_old_routes_remain_compatible_and_invalid_review_id_fails():
    http=HTTP();routes={ROOM:{'domain':'legal','flow_id':FLOW}}
    c=FlowiseClient('http://flowise:3000',{'flowise'},routes,'fake')
    asyncio.run(c.predict(http,'legal',ROOM,'u','e',[],stage='review'))
    assert http.calls[0][0].endswith(FLOW)
    with pytest.raises(FlowiseError):
        FlowiseClient('http://flowise:3000',{'flowise'},
                      {ROOM:{**routes[ROOM],'review_flow_id':'bad'}},'fake')


def graph():
    return {'nodes':[
        {'id':'p','data':{'name':'promptTemplate','inputs':{'template':'{request}','promptValues':'{"request":"{{question}}"}'}}},
        {'id':'m','data':{'name':'chatOpenAI','credential':'existing-secret-reference','inputs':{'modelName':'existing-model','basepath':'http://litellm:4000/v1','temperature':0.1,'maxTokens':2300}}},
        {'id':'c','data':{'name':'llmChain','inputs':{'model':'{{m.data.instance}}','prompt':'{{p.data.instance}}'}}}],
        'edges':[{'source':'p','target':'c'},{'source':'m','target':'c'}]}


def test_profiles_change_only_instructions_and_limits_and_escape_braces():
    original=graph();profile=load_profiles()['domains']['legal']['review']
    updated=stage_graph(original,{**profile,'instructions':'Example {quoted-data}'})
    assert original==graph()
    assert updated['edges']==original['edges']
    assert updated['nodes'][1]['data']['credential']=='existing-secret-reference'
    assert updated['nodes'][1]['data']['inputs']['modelName']=='existing-model'
    assert updated['nodes'][1]['data']['inputs']['basepath']=='http://litellm:4000/v1'
    template=updated['nodes'][0]['data']['inputs']['template']
    assert template.format(request='actual question')=='Example {quoted-data}\n\nactual question'
    changed=graph();changed['nodes'].append({'data':{'name':'memory'}})
    with pytest.raises(ValueError):stage_graph(changed,profile)
    changed=graph();changed['nodes'][0]['data']['inputs']['promptValues']='custom'
    with pytest.raises(ValueError):stage_graph(changed,profile)


@pytest.mark.parametrize('key,value',[('temperature',True),('temperature',float('nan')),('max_tokens',True),('max_tokens',50000),('instructions','')])
def test_invalid_profile_limits_are_rejected(tmp_path,key,value):
    data=load_profiles();data['domains']['legal']['draft'][key]=value
    path=tmp_path/'profiles.json';path.write_text(json.dumps(data))
    with pytest.raises(ValueError):load_profiles(path)


def management():
    p=Path(__file__).resolve().parents[2]/'scripts/upgrade_flowise.py'
    spec=importlib.util.spec_from_file_location('flowise_upgrade_tests',p)
    module=importlib.util.module_from_spec(spec)
    previous_path=list(sys.path)
    try:spec.loader.exec_module(module)
    finally:sys.path[:]=previous_path
    return module


def environment(tmp_path):
    mod=management();profile=load_profiles()['domains']
    flows={};domains={}
    for domain in ('legal','finance'):
        ident=domain+'-draft'
        before={'name':domain,'flowData':json.dumps(graph()),'type':'CHATFLOW','deployed':True,'isPublic':False,'apikeyid':'existing-key-id'}
        after={**before,'flowData':json.dumps(stage_graph(graph(),profile[domain]['draft']),ensure_ascii=False)}
        flows[ident]=dict(before,id=ident)
        domains[domain]={'id':ident,'before':before,'after':after,'review_graph':stage_graph(graph(),profile[domain]['review'])}
    state={'flowise_setup.json':{'flows':{d:d+'-draft' for d in domains}},
           'flowise_rooms.json':{ROOM:{'domain':'legal','flow_id':'legal-draft'}}}
    for name,value in state.items():(tmp_path/name).write_text(json.dumps(value))
    plan={'version':1,'status':'planned','state_before':state,'domains':domains}
    counter=[]
    def api(method,path,body=None):
        if method=='POST':
            ident='review-'+str(len(counter));counter.append(ident);flows[ident]=dict(copy.deepcopy(body),id=ident);return flows[ident]
        ident=path.rsplit('/',1)[-1]
        if method=='GET':return copy.deepcopy(flows[ident])
        if method=='PUT':flows[ident].update(copy.deepcopy(body));return flows[ident]
        if method=='DELETE':del flows[ident]
    return mod,plan,flows,api


def test_upgrade_preserves_models_and_rollback_restores_routes_and_flows(tmp_path):
    mod,plan,flows,api=environment(tmp_path);original=copy.deepcopy(flows)
    mod.apply_plan(plan,api,tmp_path,lambda _:None)
    assert plan['status']=='applied' and len(flows)==4
    routes=json.loads((tmp_path/'flowise_rooms.json').read_text())
    assert routes[ROOM]['review_flow_id']=='review-0'
    for f in flows.values():
        model=json.loads(f['flowData'])['nodes'][1]['data']
        assert model['credential']=='existing-secret-reference'
        assert model['inputs']['modelName']=='existing-model'
        assert f['apikeyid']=='existing-key-id' and f['isPublic'] is False
    mod.rollback_plan(plan,api,tmp_path,lambda _:None)
    assert flows==original and plan['status']=='rolled_back'
    assert json.loads((tmp_path/'flowise_rooms.json').read_text())==plan['state_before']['flowise_rooms.json']


def test_upgrade_refuses_concurrent_admin_edit_without_creating_flows(tmp_path):
    mod,plan,flows,api=environment(tmp_path)
    flows['legal-draft']['name']='Administrator changed this'
    with pytest.raises(ValueError):mod.apply_plan(plan,api,tmp_path,lambda _:None)
    assert len(flows)==2 and plan['status']=='planned'


def test_interrupted_apply_can_restore_created_objects(tmp_path):
    mod,plan,flows,api=environment(tmp_path)
    def failing(method,path,body=None):
        if method=='PUT':raise RuntimeError('simulated update failure')
        return api(method,path,body)
    with pytest.raises(RuntimeError):mod.apply_plan(plan,failing,tmp_path,lambda _:None)
    assert plan['status']=='applying' and len(flows)==3
    mod.rollback_plan(plan,api,tmp_path,lambda _:None)
    assert len(flows)==2 and plan['status']=='rolled_back'

@pytest.mark.parametrize('review_error',[False,True])
def test_bot_uses_review_flow_and_does_not_send_unreviewed_draft(monkeypatch,tmp_path,review_error):
    import time
    from types import SimpleNamespace
    import bot
    calls=[];out=[]
    class Flow:
        routes={ROOM:{'domain':'finance','flow_id':FLOW,'review_flow_id':REVIEW}}
        def selected(self,room,domain):return room==ROOM and domain=='finance'
        async def predict(self,*args,stage='draft'):
            calls.append(stage)
            if stage=='review' and review_error:raise FlowiseError('synthetic review failure')
            return 'DRAFT_ONLY_NOT_FOR_USER [S1]' if stage=='draft' else 'Платёж проводится без комиссии. [S1]'
    class Knowledge:
        passages=[1]
        def search(self,*args,**kwargs):
            return [{'id':'tariff:1:1','document_id':'tariff','title':'Тариф','text':'Комиссия 0 тенге.',
                     'source':'https://example.org/tariff','reviewed_on':'2026-10-13','version':'Test','section':'','page':1}]
    class Login:pass
    class Matrix:
        user='@bot:demo.local'
        def __init__(self,*args):self.callback=None
        def add_event_callback(self,callback,kind):
            if kind==bot.RoomMessageText:self.callback=callback
        async def login(self,*args,**kwargs):return Login()
        async def room_send(self,**kwargs):out.append(kwargs['content']['body'])
        async def close(self):pass
        async def sync_forever(self,**kwargs):
            await self.callback(SimpleNamespace(room_id=ROOM),SimpleNamespace(sender='@user:demo.local',
                body='Объясни условие тарифа Halyk',event_id='$stages',server_timestamp=int(time.time()*1000)+1000))
    monkeypatch.setattr(bot,'AsyncClient',Matrix);monkeypatch.setattr(bot,'LoginResponse',Login)
    monkeypatch.setattr(bot,'KnowledgeStore',lambda _:Knowledge())
    monkeypatch.setattr(bot,'read_llm_key',lambda:'dummy')
    monkeypatch.setattr(bot,'department_roster',lambda _: {})
    monkeypatch.setattr(FlowiseClient,'from_env',lambda _:Flow())
    env={'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:demo.local','MATRIX_PASSWORD':'dummy',
         'LLM_ENDPOINT':'http://litellm:4000/v1','LLM_MODEL':'test-model','OFFLINE_MODE':'0','KNOWLEDGE_DIR':'synthetic',
         'MATRIX_ROOM_ROUTES_FILE':str(tmp_path/'absent')}
    for name,value in env.items():monkeypatch.setenv(name,value)
    asyncio.run(bot.main())
    assert calls==['draft','review']
    assert 'DRAFT_ONLY_NOT_FOR_USER' not in '\n'.join(out)
    assert ('временно недоступен' if review_error else 'без комиссии') in out[-1]
