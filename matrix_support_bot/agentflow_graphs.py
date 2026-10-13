"""Stateless Agentflows V2: validate source packet -> specialist -> reviewer -> answer."""
import copy
import json
from urllib.parse import urlsplit
from flowise_profiles import load_profiles

PROTOCOL = 'sts.agentflow.v1'
MARKER = '__STS_DRAFT_TO_REVIEW__'
FIELDS = ('draft_system','draft_user','review_system','review_prefix','review_suffix')


def workflow_packet(domain, draft_messages, review_messages):
    if domain not in {'legal','finance'}:
        raise ValueError('Unknown specialist')
    if (len(draft_messages)<2 or draft_messages[0].get('role')!='system'
            or draft_messages[1].get('role')!='user' or len(review_messages)!=2
            or review_messages[0].get('role')!='system' or review_messages[1].get('role')!='user'):
        raise ValueError('Expected scoped system/user messages')
    prefix,found,suffix=review_messages[-1]['content'].rpartition(MARKER)
    if not found:raise ValueError('Review placeholder missing')
    # Retry turns belong only to this request. Serialize them as data, never merge user text into a system role.
    draft_user=draft_messages[1]['content']
    if len(draft_messages)>2:
        draft_user+='\n\nПредыдущая попытка и запрос исправления:\n'+json.dumps(draft_messages[2:],ensure_ascii=False)
    packet={'protocol':PROTOCOL,'domain':domain,'draft_system':draft_messages[0]['content'],
            'draft_user':draft_user,'review_system':review_messages[0]['content'],
            'review_prefix':prefix,'review_suffix':suffix}
    if any(not isinstance(packet[k],str) or len(packet[k])>100000 for k in FIELDS):
        raise ValueError('Invalid or oversized workflow field')
    if len(json.dumps(packet,ensure_ascii=False).encode())>256000:raise ValueError('Workflow packet too large')
    return packet


def prepare_code(domain):
    if domain not in {'legal','finance'}:raise ValueError('Unknown specialist')
    return '''const p = JSON.parse($flow.input);
if (p.protocol !== "sts.agentflow.v1" || p.domain !== DOMAIN) throw new Error("Invalid specialist packet");
const result = {};
for (const key of ["draft_system","draft_user","review_system","review_prefix","review_suffix"]) {
  if (typeof p[key] !== "string" || p[key].length > 100000) throw new Error("Invalid packet field");
  result[key] = p[key];
}
return result;'''.replace('DOMAIN',json.dumps(domain))


def build_agentflow(definitions,domain,model_config,*,allowed_model_hosts=('litellm',),profiles=None):
    profiles=(profiles or load_profiles())['domains']
    if domain not in profiles:raise ValueError('Unknown specialist')
    model=copy.deepcopy(model_config)
    url=urlsplit(model.get('basepath',''))
    if (url.scheme not in {'http','https'} or url.hostname not in allowed_model_hosts or url.username or url.password
            or url.query or url.fragment or not model.get('modelName') or not model.get('FLOWISE_CREDENTIAL_ID')):
        raise ValueError('Expected existing dedicated credential and approved model endpoint')
    model.update(streaming=False,temperature=0.1,maxTokens=2300,timeout=65000)
    def state(name):return '{{ $flow.state.'+name+' }}'
    inputs=[('startAgentflow',{'startInputType':'chatInput','startEphemeralMemory':True,'startPersistState':False,
                             'startState':[{'key':k,'value':''} for k in FIELDS]},'Вопрос из Element'),
            ('customFunctionAgentflow',{'customFunctionInputVariables':[], 'customFunctionJavascriptFunction':prepare_code(domain),
                'customFunctionUpdateState':[{'key':k,'value':'{{ output.'+k+' }}'} for k in FIELDS]},'Контекст и локальные источники'),
            ('agentAgentflow',{'agentModel':'chatOpenAI','agentModelConfig':dict(model,agentModel='chatOpenAI'),
                'agentMessages':[{'role':'system','content':profiles[domain]['draft']['instructions']+'\n'+state('draft_system')},
                                 {'role':'user','content':state('draft_user')}],
                'agentEnableMemory':False,'agentTools':[],'agentToolsBuiltInOpenAI':[],
                'agentKnowledgeDocumentStores':[],'agentKnowledgeVSEmbeddings':[],
                'agentReturnResponseAs':'assistantMessage','agentUpdateState':[]},'ЮрАгент' if domain=='legal' else 'ФинАгент'),
            ('llmAgentflow',{'llmModel':'chatOpenAI','llmModelConfig':dict(model,llmModel='chatOpenAI'),
                'llmMessages':[{'role':'system','content':profiles[domain]['review']['instructions']+'\n'+state('review_system')},
                               {'role':'user','content':state('review_prefix')+'\n{{ agentAgentflow_0 }}\n'+state('review_suffix')}],
                'llmEnableMemory':False,'llmReturnResponseAs':'assistantMessage','llmStructuredOutput':[],'llmUpdateState':[]},'Проверка фактов и условий'),
            ('directReplyAgentflow',{'directReplyMessage':'{{ llmAgentflow_0 }}'},'Ответ в Element')]
    nodes=[]
    for i,(name,values,label) in enumerate(inputs):
        definition=copy.deepcopy(definitions[name]);ident=name+'_0'
        if definition.get('name')!=name or definition.get('category')!='Agent Flows':raise ValueError('Unexpected node contract')
        params=[dict(x,id=ident+'-input-'+x['name']+'-'+x['type'],display=True) for x in definition.get('inputs',[])]
        data=dict(definition,id=ident,label=label,inputs=values,outputs={},inputParams=params,inputAnchors=[],
                  outputAnchors=[] if name=='directReplyAgentflow' else [{'id':ident+'-output-'+name,'label':label,'name':name}])
        nodes.append({'id':ident,'type':'agentFlow','position':{'x':i*350,'y':120},'data':data})
    edges=[]
    for left,right in zip(nodes,nodes[1:]):
        handle=left['id']+'-output-'+left['data']['name']
        edges.append({'id':left['id']+'-'+right['id'],'source':left['id'],'sourceHandle':handle,'target':right['id'],
                      'targetHandle':right['id'],'type':'agentFlow','data':{'sourceColor':left['data'].get('color'),
                      'targetColor':right['data'].get('color'),'isHumanInput':False}})
    return {'nodes':nodes,'edges':edges}
