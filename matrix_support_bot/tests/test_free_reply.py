import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from free_reply import FreeReplyMode, free_messages, render_free


class Content:
    def __init__(self, data): self.data=data
    async def iter_chunked(self, n):
        yield json.dumps(self.data).encode()


class Response:
    def __init__(self, data): self.content=Content(data)
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    def raise_for_status(self): pass


class HTTP:
    def __init__(self, text='Понятный ответ из общих знаний.', finish=None, error=None):
        self.text,self.finish,self.error=text,finish,error
        self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if self.error: raise self.error
        return Response({'choices':[{'message':{'content':self.text},'finish_reason':self.finish}]})


def ask(http, domain='legal', question='Объясни принцип договора', knowledge=None):
    return asyncio.run(FreeReplyMode({'!main:demo.local':domain}).reply(http,domain,question,knowledge,
        endpoint='http://gateway/v1',model='synthetic',api_key='test',previous_question=''))


def test_free_mode_is_opt_in_and_cannot_cross_specialist_routes(tmp_path):
    assert FreeReplyMode.from_env({}, {}) is None
    p=tmp_path/'free.json';p.write_text(json.dumps({'!main:demo.local':'legal'}))
    mode=FreeReplyMode.from_env({'MATRIX_FREE_REPLY_ROOMS_FILE':str(p)},{'!main:demo.local':'legal','!test:demo.local':'finance'})
    assert mode.selected('!main:demo.local','legal')
    assert not mode.selected('!main:demo.local','finance') and not mode.selected('!test:demo.local','finance')
    with pytest.raises(ValueError):FreeReplyMode.from_env({'MATRIX_FREE_REPLY_ROOMS_FILE':str(p)},{'!main:demo.local':'finance'})


@pytest.mark.parametrize('domain',['legal','finance'])
def test_no_documents_still_calls_model_once_without_legacy_gate(domain):
    http=HTTP('Общее правило можно объяснить без отдельной цитаты.')
    result=ask(http,domain=domain)
    assert len(http.calls)==1 and 'Общее правило' in result and 'не прошёл' not in result
    request=http.calls[0][1]['json'];assert request['model']=='synthetic'
    assert 'общие знания' in request['messages'][0]['content']
    assert 'Подтверждай конкретные нормы и условия только подходящими фрагментами' not in request['messages'][0]['content']


def test_broken_optional_search_does_not_replace_model_answer():
    class Broken:
        def search(self,*args,**kwargs): raise RuntimeError('offline search failed')
    http=HTTP('Понятное объяснение с уточнением существенного условия.')
    assert 'Понятное объяснение' in ask(http,knowledge=Broken())
    assert len(http.calls)==1


@pytest.mark.parametrize('text',['Привет','Добрый вечер! Помощь нужна','На какие вопросы ты можешь отвечать?','сколько выходит моя зарплата?','Сотрудник попал в больницу из за драки'])
def test_social_and_missing_data_answers_come_from_model_without_law_lookup(text):
    calls=[]
    class NoSearch:
        def search(self,*args,**kwargs):calls.append(1);return []
    http=HTTP('Собственный ответ модели на задачу.')
    assert 'Собственный ответ модели' in ask(http,question=text,knowledge=NoSearch()) and len(http.calls)==1
    assert not calls


def test_explicit_arithmetic_is_exact_and_needs_no_model():
    http=HTTP()
    assert '4 050 000' in ask(http,domain='finance',question='27% от 15 миллионов тенге')
    assert '258 000' in ask(http,domain='finance',question='Начислено 300000 тенге, удержано 42000. Сколько на руки?')
    assert not http.calls


def test_context_contains_only_passed_user_context_and_bounded_references():
    refs=[{'title':'Документ','text':'x'*8000,'source':'https://example.org','reviewed_on':'2026-01-01'}]*8
    messages=free_messages('legal','А как написать требование?',refs,'Начальник не отдаёт оплату')
    assert len(messages)==3 and messages[-1]['content']=='А как написать требование?'
    value=json.loads(messages[1]['content'].split('\n',1)[1])
    assert value['previous_questions_of_this_user']=='Начальник не отдаёт оплату'
    assert len(value['optional_local_references'])==4 and len(value['optional_local_references'][0]['text'])==4000
    assert '!человек' in messages[0]['content'] and 'НЕ ограничен' in messages[0]['content']


def test_invalid_citation_is_removed_without_discarding_useful_answer():
    p={'title':'Источник','source':'https://example.org','reviewed_on':'2026-01-01','version':'Снимок'}
    result=render_free('legal','Полезный ответ. [S1] Уточнение. [S99]',[p])
    assert '[S99]' not in result and 'Уточнение' in result and 'не прошёл' not in result
    assert 'https://example.org' in result and 'Ответ подтверждён' not in result
    from message_format import matrix_content
    content=matrix_content(result)
    assert '<sup>[1]</sup>' in content['formatted_body']


def test_partial_visible_answer_is_preserved_and_reasoning_is_not_exposed():
    result=ask(HTTP('<think>private reasoning</think>Доступная часть ответа.',finish='length'))
    assert 'private reasoning' not in result and 'Доступная часть' in result and 'ограничения объёма' in result


@pytest.mark.parametrize('kind',['timeout','empty','malformed'])
def test_generation_failure_offers_real_support_without_claiming_transfer(kind):
    http=HTTP(error=asyncio.TimeoutError()) if kind=='timeout' else HTTP('' if kind=='empty' else None)
    answer=ask(http)
    assert len(http.calls)==1 and '!человек' in answer and 'Обращение ещё не передано' in answer
    assert 'не прошёл проверку' not in answer


def test_actual_bot_routes_natural_question_directly_but_keeps_explicit_help(monkeypatch,tmp_path):
    import bot
    from flowise_client import FlowiseClient
    sent=[];http=HTTP('Да, объясню понятным языком.')
    class Session:
        async def __aenter__(self):return http
        async def __aexit__(self,*args):pass
    class Login:pass
    class Matrix:
        user='@bot:demo.local'
        def __init__(self,*args,**kwargs):self.callback=None
        def add_event_callback(self,c,k):
            if k==bot.RoomMessageText:self.callback=c
        async def login(self,*args,**kwargs):return Login()
        async def room_send(self,**kwargs):sent.append(kwargs['content']['body'])
        async def room_typing(self,*args,**kwargs):pass
        async def close(self):pass
        async def sync_forever(self,**kwargs):
            for i,q in enumerate(['На какие вопросы ты можешь отвечать?','!помощь']):
                await self.callback(SimpleNamespace(room_id='!main:demo.local'),SimpleNamespace(sender='@synthetic:demo.local',body=q,event_id='$free'+str(i),server_timestamp=int(time.time()*1000)+1000))
    routes=tmp_path/'routes.json';routes.write_text(json.dumps({'!main:demo.local':'legal'}))
    free=tmp_path/'free.json';free.write_text(routes.read_text())
    for k,v in {'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:demo.local','MATRIX_PASSWORD':'dummy','LLM_ENDPOINT':'http://gateway/v1','LLM_MODEL':'dummy','OFFLINE_MODE':'0','MATRIX_ROOM_ROUTES_FILE':str(routes),'MATRIX_FREE_REPLY_ROOMS_FILE':str(free)}.items():monkeypatch.setenv(k,v)
    monkeypatch.delenv('KNOWLEDGE_DIR',raising=False)
    monkeypatch.setattr(bot,'AsyncClient',Matrix);monkeypatch.setattr(bot,'LoginResponse',Login)
    monkeypatch.setattr(bot,'read_llm_key',lambda:'dummy');monkeypatch.setattr(bot,'department_roster',lambda _: {})
    monkeypatch.setattr(bot.aiohttp,'ClientSession',lambda **_:Session())
    monkeypatch.setattr(FlowiseClient,'from_env',lambda _:None)
    def no_legacy(*args,**kwargs):raise AssertionError('Legacy response pipeline must not run')
    monkeypatch.setattr(bot,'prepare_local_reply',no_legacy)
    asyncio.run(bot.main())
    assert len(http.calls)==1 and len(sent)==2
    assert 'Да, объясню' in sent[0] and '!сотрудники' in sent[1]
