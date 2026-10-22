import copy,json
import pytest
from agentflow_release import plan_release,create_release,activate_release,rollback_release,release_routes
from agentflow_graphs import build_agentflow


def setup():
 names=['startAgentflow','customFunctionAgentflow','agentAgentflow','llmAgentflow','directReplyAgentflow','conditionAgentflow']
 defs={n:{'name':n,'category':'Agent Flows','inputs':[]} for n in names}
 model={'modelName':'existing','basepath':'http://litellm:4000/v1','FLOWISE_CREDENTIAL_ID':'existing-credential'}
 flows={d:{'id':d,'name':d,'flowData':json.dumps(build_agentflow(defs,d,model)),'type':'AGENTFLOW','deployed':True,'isPublic':False,'apikeyid':'existing-key'} for d in ['legal','finance']}
 routes={'!'+d+':demo.local':{'domain':d,'flow_id':d,'engine':'agentflow_v2'} for d in flows}
 def api(method,path,body=None):
  ident=path.rsplit('/',1)[-1]
  if path.startswith('/nodes/'):return copy.deepcopy(defs[ident])
  if method=='GET':return copy.deepcopy(flows[ident])
  assert method=='POST';ident='candidate-'+str(len(flows));flows[ident]=dict(copy.deepcopy(body),id=ident);return copy.deepcopy(flows[ident])
 return flows,routes,api


def test_candidate_flows_do_not_change_active_rooms_until_explicit_activation():
 flows,routes,api=setup();original=copy.deepcopy(flows);plan=plan_release(routes,api)
 assert plan['phase']=='planned' and flows==original
 create_release(plan,api,lambda p:None)
 assert plan['phase']=='created' and routes==plan['routes_before']
 assert all(flows[k]==v for k,v in original.items())
 after=[];activate_release(plan,routes,api,lambda p:None,lambda v:after.append(v))
 assert plan['phase']=='active' and all(v['quality_contract']=='sts.claims.v1' for v in after[0].values())
 assert all(flows[k]==v for k,v in original.items())
 rollback_release(plan,after[0],lambda p:None,lambda v:after.append(v))
 assert after[-1]==routes and all(flows[k]==v for k,v in original.items())


@pytest.mark.parametrize('target',['candidate','original','routes'])
def test_independent_changes_block_activation_without_changing_routes(target):
 flows,routes,api=setup();plan=plan_release(routes,api);create_release(plan,api,lambda p:None)
 if target=='candidate':flows[plan['flows']['legal']['candidate_id']]['isPublic']=True
 elif target=='original':flows['legal']['name']='manual edit'
 else:routes['!legal:demo.local']['flow_id']='another-flow'
 writes=[]
 with pytest.raises(ValueError):activate_release(plan,routes,api,lambda p:None,lambda v:writes.append(v))
 assert not writes
