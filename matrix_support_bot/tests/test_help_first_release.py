"""Execute the real JS gate, Python verifier and renderer on mixed-validity replies.

These synthetic examples test release behaviour, not legal interpretation.
"""
import asyncio
import copy
import json
from pathlib import Path
import subprocess

import pytest

from agentflow_claims import accept_quality_response, make_quality_packet, units
from agentflow_graphs import MARKER
from agentflow_quality_graph import build_quality_agentflow
from agents import render
from local_reply import prepare_local_reply
from review_result import ReviewDetails, ReviewedAnswer


SOURCE = {'id': 'S1', 'text': 'Синтетический пример: организация направляет письменный ответ.'}
PAIR = ([{'role':'system','content':'sources'},{'role':'user','content':'question'}],
        [{'role':'system','content':'review'},{'role':'user','content':MARKER}])


def check(answer, rows, *, kind='situation', sources=None, final=True, constraints=None, question='Как решить вопрос?'):
    quality = {'sources': sources or [], 'question': question, 'previous_question':'',
               'question_type':kind, 'constraints':constraints or []}
    audit = {'verdict':'approve' if all(r['status']!='unsupported' for r in rows) else 'revise', 'checks':rows}
    code = (Path(__file__).resolve().parents[1]/'agents/quality/claim_gate.js').read_text()
    # Values go through JSON/stdin, not shell interpolation.
    script = ('const input=JSON.parse(require("fs").readFileSync(0,"utf8"));'
              'const $flow=input.flow,$audit=input.audit,$final=input.final;'
              'process.stdout.write(JSON.stringify((function(){'+code+'})()));')
    result = subprocess.run(['node','-e',script], input=json.dumps({
        'flow':{'state':{'draft':answer,'quality_json':json.dumps(quality)}},
        'audit':json.dumps(audit),'final':'yes' if final else 'no'}),
        text=True, check=True, capture_output=True)
    output=json.loads(result.stdout)
    if output['audit_ok']=='yes':
        verified=accept_quality_response(output['response'],{'quality':quality})
        return output, verified
    return output, None


def advice(ident, *, standalone=True):
    return {'unit_id':ident,'status':'advice','proofs':[],'standalone':standalone}


def unsupported(ident):
    return {'unit_id':ident,'status':'unsupported','proofs':[],'standalone':True}


def supported(ident):
    return {'unit_id':ident,'status':'supported','proofs':[{'source_id':'S1','quote':SOURCE['text']}], 'standalone':True}


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('kind',['definition','situation','calculation'])
def test_missing_deadline_does_not_destroy_useful_steps(domain,kind):
    answer='Сохраните договор и переписку.\nЗапросите письменное объяснение.\nОтвет обязаны дать в течение 99 дней. [S1]'
    rows=[advice('C1'),advice('C2'),unsupported('C3')]
    output, verified=check(answer,rows,kind=kind,sources=[SOURCE])
    assert verified is not None and verified.review.partial
    assert verified.review.omitted_topics==('deadline','rule')
    html=render(domain,verified,'Как получить объяснение?',[],review=verified.review)
    assert 'Сохраните договор' in html and 'Запросите письменное' in html
    assert '99' not in html and '[S1]' not in html
    assert html.index('Сохраните договор')<html.index('Точный срок пока не подтверждён')
    assert 'обратитесь к специалисту' not in html and 'Не удалось' not in html
    # The first gate still requests the one bounded repair before filtering.
    assert check(answer,rows,kind=kind,sources=[SOURCE],final=False)[1] is None


@pytest.mark.parametrize('kind',['definition','situation','calculation','document'])
def test_empty_search_allows_checked_guidance_but_not_fabricated_sources(kind):
    answer='Сохраните переписку по вопросу.\nУточните желаемый результат.'
    output, verified=check(answer,[advice('C1'),advice('C2')],kind=kind)
    assert verified is not None and not verified.review.partial
    assert 'Не удалось' not in render('legal',verified,'Помоги разобраться',[],review=verified.review)
    invented='Организация направляет письменный ответ. [S1]'
    assert check(invented,[supported('C1')],kind=kind)[1] is None


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('has_store',[True,False])
def test_only_protected_route_may_generate_after_empty_search(domain,has_store):
    class EmptyStore:
        def search(self,*args,**kwargs):return []
    store=EmptyStore() if has_store else None
    q='Мне предлагают изменить условия сотрудничества. Помоги разобраться.'
    reply=prepare_local_reply(domain,q,store,allow_model_without_sources=True)
    assert reply.answer is None and reply.evidence==[] and reply.mechanism=='requires_guidance_model'
    if has_store:
        legacy=prepare_local_reply(domain,q,store)
        assert legacy.answer is not None and legacy.mechanism=='no_evidence_fallback'


def test_all_relevant_supported_facts_survive_an_unproven_fee():
    answer='Организация направляет письменный ответ. [S1]\nКомиссия составляет 99 тенге. [S1]\nСохраните переписку.'
    _, verified=check(answer,[supported('C1'),unsupported('C2'),advice('C3')],sources=[SOURCE])
    assert verified is not None and '99' not in verified
    assert 'письменный ответ' in verified and 'Сохраните переписку' in verified
    evidence=[dict(SOURCE,id='synthetic:1',title='Synthetic fixture',reviewed_on='2026-10-30',source='https://example.org/fixture')]
    result=render('finance',verified,'Что делать?',evidence,review=verified.review)
    assert 'Точная сумма, ставка или комиссия пока не подтверждена' in result
    assert 'Локальные материалы для проверки' in result and '99' not in result


@pytest.mark.parametrize('bad','standalone,incomplete,duplicate,all_unsupported,dependent'.split(','))
def test_filtering_never_invents_independence_or_accepts_a_broken_audit(bad):
    answer='Сохраните договор.\nОтвет обязаны дать в течение 99 дней.'
    rows=[advice('C1'),unsupported('C2')]
    if bad=='standalone':rows[0]['standalone']=False
    elif bad=='incomplete':rows.pop()
    elif bad=='duplicate':rows[1]['unit_id']='C1'
    elif bad=='all_unsupported':rows[0]=unsupported('C1')
    else:
        answer='В этом случае сохраните договор.\nОтвет обязаны дать в течение 99 дней.'
    assert check(answer,rows)[1] is None


def test_missing_exception_removes_the_legal_conclusion_and_keeps_only_independent_advice():
    constraints=[{'id':'synthetic-exception','patterns':['кроме'],'guidance':'Keep the exception.'}]
    answer='Организация направляет письменный ответ. [S1]\nСохраните переписку.'
    _, verified=check(answer,[supported('C1'),advice('C2')],sources=[SOURCE],constraints=constraints)
    assert verified is not None and str(verified)=='Сохраните переписку.'
    assert verified.review.omitted_topics==('conditions',)
    assert check(answer.split('\n')[0],[supported('C1')],sources=[SOURCE],constraints=constraints)[1] is None
    # Transport cannot accept the stripped-down conclusion even with a forged approved flag.
    packet={'quality':{'sources':[SOURCE],'question':'q','previous_question':'','question_type':'situation','constraints':constraints}}
    forged={'contract':'sts.claims.v1','status':'approved','question_type':'situation','answer':answer.split('\n')[0],
            'audit':{'verdict':'approve','checks':[supported('C1')]}}
    with pytest.raises(ValueError,match='missing_qualification'):accept_quality_response(json.dumps(forged),packet)


def test_safety_actions_remain_required_even_without_a_legal_conclusion():
    constraints=[{'id':'immediate-threat-safety','patterns':['безопасност|экстренн'],'guidance':'Safety first.'}]
    assert check('Сохраните переписку.',[advice('C1')],constraints=constraints)[1] is None
    assert check('Обеспечьте безопасность при непосредственной угрозе.',[advice('C1')],constraints=constraints)[1] is not None


def test_partial_document_never_looks_like_a_complete_letter():
    answer='Сохраните переписку.\nКому: [организация]\nПрошу вернуть деньги.\nОтвет обязаны дать в течение 99 дней.'
    _, verified=check(answer,[advice('C1'),advice('C2'),advice('C3'),unsupported('C4')],kind='document')
    assert str(verified)=='Сохраните переписку.'
    assert 'Кому:' not in verified and 'Прошу вернуть' not in verified


@pytest.mark.parametrize('bad_request',[False,True])
def test_complete_neutral_letter_is_preserved_as_a_group_after_bad_intro_is_removed(bad_request):
    request='Прошу сообщить причину отказа.' if not bad_request else 'Прошу начислить пеню в течение 99 дней.'
    answer=('Письмо обязательно рассмотрят в течение 99 дней.\nКому: [организация]\nОт: [ФИО]\n'
            'Тема: [предмет обращения]\n'+request+'\nДата: [дата]\nПодпись: [подпись]')
    rows=[unsupported('C1')]+[advice('C'+str(i),standalone=False) for i in range(2,8)]
    # Some auditors mark fields as dependent. A complete neutral template is
    # validated together rather than pretending that a signature stands alone.
    assert len(units(answer))==7
    _,verified=check(answer,rows,kind='document')
    if bad_request:
        assert verified is None
    else:
        assert verified is not None and verified.review.partial
        assert 'Кому:' in verified and 'Подпись:' in verified and 'Прошу сообщить' in verified
        assert '99' not in verified and 'обязательно рассмотрят' not in verified
        assert 'Не удалось' not in render('legal',verified,'Напиши письмо',[],review=verified.review)


def test_reviewed_user_facts_can_accompany_guidance_without_becoming_a_law():
    q='Я отправил письмо работодателю.'
    answer=q+'\nСохраните копию обращения.'
    rows=[{'unit_id':'C1','status':'user_context','proofs':[],'user_quote':q,'standalone':True},advice('C2')]
    _, verified=check(answer,rows,question=q)
    assert verified is not None and verified.review.user_context==(q,)
    assert 'Сохраните копию' in render('legal',verified,q,[],review=verified.review)
    forged=copy.deepcopy(rows)
    forged[0]['user_quote']='Банк обязан вернуть деньги.'
    _, filtered=check(answer,forged,question=q)
    assert filtered is not None and filtered.review.partial
    assert str(filtered)=='Сохраните копию обращения.' and filtered.review.user_context==()


@pytest.mark.parametrize('topic',[['deadline; 99 дней'],['unknown'],['deadline','deadline'],'deadline'])
def test_gap_metadata_cannot_echo_rejected_text_or_smuggle_instructions(topic):
    value={'contract':'sts.claims.v1','question_type':'situation','status':'approved','answer':'Сохраните договор.',
           'audit':{'verdict':'approve','checks':[advice('C1')]},'partial':True,'omitted_topics':topic}
    with pytest.raises(ValueError,match='omitted topics'):
        accept_quality_response(json.dumps(value),{'quality':{'question':'q','previous_question':'','question_type':'situation','sources':[]}})


def test_partial_letter_help_is_not_discarded_by_the_full_letter_heuristic(monkeypatch):
    import quality_reply as module
    partial=ReviewedAnswer('Сохраните договор и подтверждения оплаты.',ReviewDetails((),True,'document',(),('rule',)))
    class Flow:
        async def run_workflow(self,*args,**kwargs):return partial
    monkeypatch.setattr(module,'research_evidence',lambda *args:[])
    result=asyncio.run(module.quality_reply(Flow(),None,'legal','room','user','event','Напиши иск в суд','',[],None))
    assert 'Сохраните договор' in result.answer and 'Правовое основание' in result.answer


def test_generated_graph_accepts_empty_sources_and_keeps_auditing():
    names=['startAgentflow','customFunctionAgentflow','agentAgentflow','llmAgentflow','directReplyAgentflow','conditionAgentflow']
    graph=build_quality_agentflow({n:{'name':n,'category':'Agent Flows','inputs':[]} for n in names},'legal',{'modelName':'synthetic','basepath':'http://litellm:4000/v1','FLOWISE_CREDENTIAL_ID':'reference'})
    prepare=graph['nodes'][1]['data']['inputs']['customFunctionJavascriptFunction']
    packet=make_quality_packet('legal',*PAIR,{'question':'Помоги разобраться','evidence':[]})
    script='const $flow={input:JSON.stringify(JSON.parse(require("fs").readFileSync(0,"utf8")))};process.stdout.write(JSON.stringify((function(){'+prepare+'})()));'
    result=subprocess.run(['node','-e',script],input=json.dumps(packet),text=True,check=True,capture_output=True)
    assert json.loads(json.loads(result.stdout)['quality_json'])['sources']==[]
    assert len(graph['nodes'])==13


@pytest.mark.parametrize('domain',['legal','finance'])
def test_matrix_event_reaches_protected_workflow_despite_empty_search(domain,monkeypatch,tmp_path):
    """Exercise actual bot routing; transport/model are isolated test doubles."""
    import time
    from types import SimpleNamespace as NS
    import bot
    from flowise_client import FlowiseClient
    output=[]
    calls=[]
    room='!synthetic:test'
    class Login:pass
    class Matrix:
        user='@bot:test'
        def __init__(self,*args):self.callback=None
        def add_event_callback(self,callback,kind):
            if kind==bot.RoomMessageText:self.callback=callback
        async def login(self,*args,**kwargs):return Login()
        async def room_send(self,**kwargs):output.append(kwargs['content'])
        async def close(self):pass
        async def sync_forever(self,**kwargs):
            await self.callback(NS(room_id=room),NS(sender='@alice:test',body='Мне предлагают изменить условия сотрудничества. Помоги разобраться.',event_id='$synthetic',server_timestamp=int(time.time()*1000)+1000))
    class EmptyStore:
        passages=[]
        def search(self,*args,**kwargs):return []
    class Flow:
        routes={room:{'domain':domain,'quality_contract':'sts.claims.v1','engine':'agentflow_v2'}}
        def is_workflow(self,*args):return True
        async def run_workflow(self,*args,**kwargs):
            calls.append(kwargs['context'])
            assert kwargs['context']['evidence']==[]
            return ReviewedAnswer('Запросите письменное описание предлагаемых изменений.\nУточните, с чем вы не согласны.',ReviewDetails((),False,'situation'))
    settings={'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:test','MATRIX_PASSWORD':'synthetic',
              'LLM_ENDPOINT':'http://litellm:4000/v1','LLM_MODEL':'synthetic','KNOWLEDGE_DIR':'synthetic',
              'OFFLINE_MODE':'0','MATRIX_ROOM_ROUTES_FILE':str(tmp_path/'absent'),
              'MATRIX_INBOX_DB':str(tmp_path/'queue.sqlite3'),'HANDOFF_DB':str(tmp_path/'handoff.sqlite3')}
    for k,v in settings.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(bot,'AsyncClient',Matrix)
    monkeypatch.setattr(bot,'LoginResponse',Login)
    monkeypatch.setattr(bot,'department_roster',lambda _: {})
    monkeypatch.setattr(bot,'read_llm_key',lambda:'synthetic')
    monkeypatch.setattr(bot,'KnowledgeStore',lambda _:EmptyStore())
    monkeypatch.setattr(FlowiseClient,'from_env',lambda _:Flow())
    asyncio.run(bot.main())
    replies=[o for o in output if 'Запросите письменное' in o.get('body','')]
    assert len(calls)==1 and len(replies)==1
    assert replies[0].get('formatted_body') and 'Не удалось' not in replies[0]['body']
