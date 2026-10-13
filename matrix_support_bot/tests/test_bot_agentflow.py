"""Agentflow calls replace the two legacy stage calls; retries keep source scope."""
import asyncio,time
from types import SimpleNamespace
import pytest
from flowise_client import FlowiseClient

@pytest.mark.parametrize('invalid_first',[False,True])
def test_bot_calls_one_complete_workflow_per_attempt(monkeypatch,tmp_path,invalid_first):
    import bot
    calls=[];out=[]
    class Flow:
        routes={'!v2:demo.local':{'domain':'finance','flow_id':'synthetic','engine':'agentflow_v2'}}
        def selected(self,*args):return True
        def is_workflow(self,*args):return True
        async def predict(self,*args,**kwargs):raise AssertionError('Legacy prediction must never run')
        async def run_workflow(self,http,domain,room,sender,event,draft,review):
            calls.append((draft,review))
            assert len(review)==2 and review[0]['role']=='system'
            assert 'tariff:1:1' in draft[0]['content'] and 'tariff:1:1' in review[0]['content']
            assert '__STS_DRAFT_TO_REVIEW__' in review[-1]['content']
            return 'Неизвестное право. [S9]' if invalid_first and len(calls)==1 else 'Комиссия составляет 0 тенге. [S1]'
    class Knowledge:
        passages=[1]
        def search(self,*args,**kwargs):return [{'id':'tariff:1:1','document_id':'tariff','title':'Тариф','text':'Комиссия 0 тенге.',
            'source':'https://example.org/tariff','reviewed_on':'2026-10-13','version':'Test','section':'','page':1}]
    class Login:pass
    class Matrix:
        user='@bot:demo.local'
        def __init__(self,*args):self.callback=None
        def add_event_callback(self,c,k):
            if k==bot.RoomMessageText:self.callback=c
        async def login(self,*args,**kwargs):return Login()
        async def room_send(self,**kwargs):out.append(kwargs['content']['body'])
        async def close(self):pass
        async def sync_forever(self,**kwargs):await self.callback(SimpleNamespace(room_id='!v2:demo.local'),
            SimpleNamespace(sender='@synthetic:demo.local',body='Поясни условие тарифа Halyk',event_id='$case',server_timestamp=int(time.time()*1000)+1000))
    for name,value in {'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:demo.local','MATRIX_PASSWORD':'dummy',
        'LLM_ENDPOINT':'http://litellm:4000/v1','LLM_MODEL':'dummy','KNOWLEDGE_DIR':'synthetic','OFFLINE_MODE':'0','MATRIX_ROOM_ROUTES_FILE':str(tmp_path/'absent')}.items():monkeypatch.setenv(name,value)
    monkeypatch.setattr(bot,'AsyncClient',Matrix);monkeypatch.setattr(bot,'LoginResponse',Login)
    monkeypatch.setattr(bot,'read_llm_key',lambda:'dummy');monkeypatch.setattr(bot,'department_roster',lambda _: {})
    monkeypatch.setattr(bot,'KnowledgeStore',lambda _:Knowledge());monkeypatch.setattr(FlowiseClient,'from_env',lambda _:Flow())
    asyncio.run(bot.main())
    assert len(calls)==(2 if invalid_first else 1)
    assert 'Комиссия составляет 0 тенге' in out[-1] and '[S9]' not in out[-1]
    if invalid_first:assert any(m['role']=='assistant' for m in calls[1][0])
