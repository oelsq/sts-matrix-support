"""Real protected Agentflow V2 + mock model. No genuine model key or Matrix I/O."""
import argparse,asyncio,json,secrets,sys
from pathlib import Path
from http.cookiejar import CookieJar
from urllib.request import Request,HTTPCookieProcessor,build_opener
from urllib.error import HTTPError
import aiohttp
from aiohttp import web
sys.path.append('/app')
from agentflow_graphs import build_agentflow,MARKER
from flowise_client import FlowiseClient
from flowise_profiles import load_profiles

async def exercise(flow_id,key):
    seen=[]
    async def completion(request):
        data=await request.json();seen.append(data)
        reviewing='EDITOR_PHASE' in json.dumps(data.get('messages',[]))
        return web.json_response({'id':'chatcmpl-sts-test','object':'chat.completion','created':1,'model':'transport-test',
            'choices':[{'index':0,'message':{'role':'assistant','content':'FINAL_REVIEWED [S1]' if reviewing else 'DRAFT_FOR_REVIEW [S1]'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    app=web.Application();app.router.add_post('/v1/chat/completions',completion)
    runner=web.AppRunner(app);await runner.setup()
    try:
        await web.TCPSite(runner,'0.0.0.0',18090).start()
        async with aiohttp.ClientSession() as http:
            async with http.post('http://flowise:3000/api/v1/prediction/'+flow_id,json={'question':'auth probe'}) as res:
                assert res.status>=400 and not seen
            client=FlowiseClient('http://flowise:3000',{'flowise'},
                {'!transport:demo.local':{'domain':'legal','flow_id':flow_id,'engine':'agentflow_v2'}},key)
            for sender in ['@synthetic-a:demo.local','@synthetic-b:demo.local']:
                value=await client.run_workflow(http,'legal','!transport:demo.local',sender,'$event',
                    [{'role':'system','content':'SOURCE_DATA [S1]'},{'role':'user','content':'Public test question'}],
                    [{'role':'system','content':'REVIEW_RULE SOURCE_DATA [S1]'},{'role':'user','content':'Review '+MARKER}])
                assert value=='FINAL_REVIEWED [S1]',repr(value)
            assert len(seen)==4,'Workflow must make one draft and one review call per request'
            for draft,review in zip(seen[::2],seen[1::2]):
                assert 'DRAFT_PHASE' in json.dumps(draft['messages'])
                assert 'EDITOR_PHASE' in json.dumps(review['messages'])
                assert 'DRAFT_FOR_REVIEW' in json.dumps(review['messages'])
                assert 'SOURCE_DATA' in json.dumps(review['messages'])
                assert '@synthetic' not in json.dumps(review['messages'])
            print('AGENTFLOW_V2_TRANSPORT_PASS: auth blocked, two isolated requests, draft then review, final output only')
    finally:await runner.cleanup()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state-dir',default='/app/state');args=parser.parse_args()
    admin=json.loads((Path(args.state_dir)/'flowise_admin.json').read_text());opener=build_opener(HTTPCookieProcessor(CookieJar()))
    def api(method,path,body=None):
        req=Request('http://flowise:3000/api/v1'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','x-request-from':'internal'},method=method)
        try:
            with opener.open(req,timeout=45) as response:
                data=response.read();return json.loads(data) if data else None
        except HTTPError as e:raise RuntimeError('Flowise test HTTP '+str(e.code)) from None
    api('POST','/auth/login',admin);name='STS temporary V2 transport '+secrets.token_hex(4)
    key=credential=flow=None
    try:
        keys=api('POST','/apikey',{'keyName':name,'permissions':['chatflows:execute']})
        if isinstance(keys,dict):keys=keys['data']
        key=next(k for k in keys if k['keyName']==name)
        credential=api('POST','/credentials',{'name':name,'credentialName':'openAIApi','plainDataObj':{'openAIApiKey':'not-a-real-key'}})
        defs={n:api('GET','/nodes/'+n) for n in ['startAgentflow','customFunctionAgentflow','agentAgentflow','llmAgentflow','directReplyAgentflow']}
        profiles=load_profiles();profiles['domains']['legal']['draft']['instructions']='DRAFT_PHASE';profiles['domains']['legal']['review']['instructions']='EDITOR_PHASE'
        graph=build_agentflow(defs,'legal',{'basepath':'http://bot:18090/v1','modelName':'transport-test','FLOWISE_CREDENTIAL_ID':credential['id']},allowed_model_hosts=('bot',),profiles=profiles)
        flow=api('POST','/chatflows',{'name':name,'flowData':json.dumps(graph),'type':'AGENTFLOW','isPublic':False,'deployed':True,'apikeyid':key['id']})
        asyncio.run(exercise(flow['id'],key['apiKey']))
    finally:
        failures=[]
        for kind,item in [('chatflows',flow),('credentials',credential),('apikey',key)]:
            if item:
                try:api('DELETE','/'+kind+'/'+item['id'])
                except RuntimeError:failures.append(kind)
        if failures:raise RuntimeError('Temporary object cleanup failed: '+','.join(failures))
        print('Temporary Agentflow, dummy credential and test key removed')
if __name__=='__main__':main()
