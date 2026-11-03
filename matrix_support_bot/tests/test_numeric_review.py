import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from agentflow_claims import make_quality_packet, accept_quality_response, validate_audit
from agents import render
from message_format import matrix_content
from numeric_grounding import numeric_facts
from review_result import ReviewDetails

GATE=Path(__file__).resolve().parents[1]/'agents/quality/claim_gate.js'
SOURCE={'id':'tariff:1:1','title':'Проверочный тариф','text':'Комиссия составляет 0 тенге. Лимит перевода составляет 100 тенге.','source':'https://example.org/tariff','reviewed_on':'2026-10-22','version':'Проверочный пример','page':1,'section':''}
PAIR=([{'role':'system','content':'system'},{'role':'user','content':'user'}],[{'role':'system','content':'review'},{'role':'user','content':'__STS_DRAFT_TO_REVIEW__'}])

def gate(answer,source,*,partial=False):
    audit={'answer_unit_ids':'C1','verdict':'approve','checks':[{'unit_id':'C1','status':'supported','proofs':[{'source_id':'S1','quote':source}]}]}
    quality={'sources':[{'id':'S1','text':source}],'question':'Уточните значение','previous_question':'','question_type':'definition'}
    script='const $flow='+json.dumps({'state':{'draft':answer,'quality_json':json.dumps(quality)}})+';const $audit='+json.dumps(json.dumps(audit))+';console.log(JSON.stringify((function(){'+GATE.read_text()+'})()));'
    result=json.loads(subprocess.check_output(['node','-e',script],text=True))
    return result,validate_audit(answer,audit,quality['sources'])

@pytest.mark.skipif(not shutil.which('node'),reason='Node unavailable')
@pytest.mark.parametrize('source,answer,valid',[
    (SOURCE['text'],'Комиссия составляет 100 тенге. [S1]',False),
    (SOURCE['text'],'Комиссия составляет 0 тенге. [S1]',True),
    ('Срок рассмотрения составляет 15 рабочих дней. Срок обжалования составляет 30 календарных дней.','Срок рассмотрения составляет 30 календарных дней. [S1]',False),
    ('Срок рассмотрения составляет 15 рабочих дней.','Срок рассмотрения составляет 15 календарных дней. [S1]',False),
    ('Комиссия составляет 10 долларов.','Комиссия составляет 10 тенге. [S1]',False),
    ('Комиссия составляет не более 10 тенге.','Комиссия составляет 10 тенге. [S1]',False),
    ('Комиссия составляет не более 10 тенге.','Комиссия составляет не более 10 тенге. [S1]',True),
    ('Комиссия составляет двадцать три тенге.','Комиссия составляет 23 тенге. [S1]',True),
    ('Процентная ставка составляет 12,50 %.','Процентная ставка составляет 12.5 процентов. [S1]',True),
    ('Задолженность составляет 100 000 тг.','Задолженность составляет 100000 тенге. [S1]',True),
    ('Срок обжалования составляет 3 года.','Срок обжалования составляет 3 лет. [S1]',True),
])
def test_actual_node_gate_and_python_bind_metric_value_unit_and_bounds(source,answer,valid):
    result,errors=gate(answer,source)
    assert (result['audit_ok']=='yes') is valid,(result,errors)
    assert (not errors) is valid,errors
    if not valid:assert any('unproven_numeric_relation' in e for e in errors)

@pytest.mark.skipif(not shutil.which('node'),reason='Node unavailable')
def test_numeric_parsers_and_shared_metric_spec_agree():
    prefix=GATE.read_text().split('const answer = $flow.state.draft;')[0]
    samples=[SOURCE['text'],'Комиссия 23 тг.','Срок обжалования пять лет.','Срок рассмотрения 15 рабочих дней.','Комиссия не более 0,50 процента.','Зарплата 1\u202f200\u00a0000 тенге.','ГЭСВ 17 процентов. Номинальная ставка 12 процентов.']
    out=json.loads(subprocess.check_output(['node','-e',prefix+'console.log(JSON.stringify({metrics:NUMERIC_METRICS,facts:'+json.dumps(samples)+'.map(numericFacts)}));'],text=True))
    assert out['metrics']==json.loads(GATE.with_name('numeric_metrics.json').read_text())
    assert out['facts']==[[list(f) for f in numeric_facts(s)] for s in samples]

def reviewed(answer,quote,partial=False,source=None):
    source=source or SOURCE
    packet=make_quality_packet('finance',*PAIR,{'question':'Какая комиссия?','evidence':[source]})
    response={'contract':'sts.claims.v1','question_type':packet['quality']['question_type'],'status':'approved','answer':answer,'partial':partial,'audit':{'answer_unit_ids':'C1','verdict':'approve','checks':[{'unit_id':'C1','status':'supported','proofs':[{'source_id':'S1','quote':quote}]}]}}
    return accept_quality_response(json.dumps(response),packet)

def test_exact_approved_quote_survives_transport_render_and_matrix_html():
    source=dict(SOURCE,text='Комиссия при переводах регулируется отдельно.\nОплата коммунальных услуг: комиссия составляет 0 тенге.\nЛимит перевода составляет 100 тенге.')
    quote='Оплата коммунальных услуг: комиссия составляет 0 тенге.'
    answer=reviewed('Комиссия составляет 0 тенге. [S1]',quote,source=source)
    output=render('finance',answer,'Какая комиссия при переводах?',[source])
    content=matrix_content(output)
    assert 'Цитата из документа: «'+quote+'»' in output
    assert 'регулируется отдельно' not in output
    assert quote in content['body'] and quote in content['formatted_body']
    assert answer.review.quotes==( ('S1',quote), )

@pytest.mark.parametrize('partial',[False,True])
def test_partial_status_is_visible_without_reintroducing_removed_claims(partial):
    answer=reviewed('Комиссия составляет 0 тенге. [S1]','Комиссия составляет 0 тенге.',partial)
    output=render('finance',answer,local_evidence=[SOURCE])
    assert ('Ответ подтверждён частично' in output) is partial
    assert 'Комиссия составляет 100' not in output

@pytest.mark.parametrize('quote,alias',[('Выдуманная цитата про комиссию.','S1'),('Комиссия составляет 0 тенге.','S2')])
def test_renderer_refuses_changed_or_misbound_verified_provenance(quote,alias):
    with pytest.raises(ValueError):render('finance','Комиссия составляет 0 тенге. [S1]',local_evidence=[SOURCE],review=ReviewDetails(((alias,quote),)))

def test_explicit_version_quote_is_preserved_and_review_date_is_not_invented():
    source=dict(SOURCE,version='Тариф действует с 15.09.2026')
    quote='Редакция или версия документа: Тариф действует с 15.09.2026'
    answer=reviewed('Тариф действует с 15 сентября 2026 года. [S1]',quote,source=source)
    output=render('finance',answer,local_evidence=[source])
    assert 'Цитата из документа: «'+quote+'»' in output
    assert 'действует с 22' not in output


@pytest.mark.skipif(not shutil.which('node'),reason='Node unavailable')
@pytest.mark.parametrize('text',[
    'Уточните, что договор не может ограничивать получение содержания.',
    'Напишите, что односторонний отказ не допускается.',
    'Проверьте, что договор регулирует имущественные права детей.',
])
def test_legal_assertion_inside_an_action_is_not_uncited_advice_in_python_or_node(text):
    from agentflow_claims import safe_advice
    assert not safe_advice(text)
    prefix=GATE.read_text().split('const answer = $flow.state.draft;')[0]
    result=json.loads(subprocess.check_output(['node','-e',prefix+'console.log(JSON.stringify(safeAdvice('+json.dumps(text)+')));'],text=True))
    assert result is False


def test_short_regulatory_definition_does_not_request_a_situation_action_plan():
    from agentflow_claims import question_type
    assert question_type('Что регулирует брачный договор?')=='definition'


def test_verified_calculation_clarification_preserves_partial_status_without_fake_citations():
    packet=make_quality_packet('finance',*PAIR,{'question':'Рассчитай переплату кредита','evidence':[SOURCE]})
    response={'contract':'sts.claims.v1','question_type':'calculation','status':'approved','answer':'Пришлите график платежей.\nУточните комиссии.','partial':True,'audit':{'answer_unit_ids':'C1','verdict':'approve','checks':[{'unit_id':'C1','status':'advice'},{'unit_id':'C2','status':'advice'}]}}
    answer=accept_quality_response(json.dumps(response),packet)
    output=render('finance',answer,local_evidence=[SOURCE])
    assert 'Ответ подтверждён частично' in output and 'Пришлите график платежей' in output
    assert 'Не удалось' not in output and 'Локальные материалы' not in output
