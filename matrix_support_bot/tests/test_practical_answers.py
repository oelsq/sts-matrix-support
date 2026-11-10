"""Task-completion regressions; no claim of expert legal approval."""
import json
import subprocess

import pytest

from local_reply import prepare_local_reply
from response_guidance import is_document_request, request_clarification, retrieval_question
from task_preparation import preparation_reply, draft_task_text
from answer_focus import practical_response_focus
from claim_validation import safe_advice
from local_claim_code import unit_code


class NoLookup:
    def search(self,*args,**kwargs):raise AssertionError('A neutral organizational request must not look up a statute')


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question,previous,topic',[
    ('Напиши нейтральное письмо контрагенту с просьбой объяснить отказ, без статей и законных сроков.','', 'отказ'),
    ('Помоги написать запрос в банк: списали деньги, хочу расшифровку операции, без статей и законных сроков.','', 'операции'),
    ('А что ему конкретно написать?','Начальник не отдаёт деньги за работу','оплаты'),
    ('А что им написать?','Банк списал непонятную комиссию','списания'),
    ('Напиши короткий запрос работодателю о расчёте выплаты. Не указывай законные сроки и статьи.','', 'расчёт'),
])
def test_deliver_the_requested_text_without_waiting_for_a_law_search(domain,question,previous,topic):
    result=prepare_local_reply(domain,question,NoLookup(),search_question=retrieval_question(question,previous),previous_question=previous)
    assert result.mechanism=='organizational_preparation'
    assert topic in result.answer
    for field in ['Кому:','От:','Прошу','Дата:','Подпись:']:assert field in result.answer
    assert '[S1]' not in result.answer and '10 дней' not in result.answer
    assert 'обязан вернуть' not in result.answer
    if 'банк' in (question+previous).casefold():assert 'Кому: [банк]' in result.answer


@pytest.mark.parametrize('question',[
    'Напиши иск о взыскании задолженности без статей и законных сроков.',
    'Напиши запрос о расчёте выплаты, без статей и законных сроков, но укажи, что работодатель обязан выплатить пеню.',
    'Напиши письмо и укажи обязательный срок ответа по закону.',
    'Помоги написать письмо в банк: он обязан вернуть комиссию за 10 дней.',
    'Напиши претензию в банк о возврате удержанной комиссии.',
    'Напиши запрос о расчёте выплаты по статье 113, без законных сроков.',
    'Напиши запрос, как обжаловать отказ в суде, без статей.',
    'Составь запрос расчёта выплаты и рассчитай точную пеню.',
    'Напиши письмо о расчёте выплаты без договора и укажи законный срок.',
])
def test_neutral_format_exclusions_never_hide_a_positive_legal_demand(question):
    assert preparation_reply('legal',question) is None


@pytest.mark.parametrize('question',[
    'А что ему конкретно написать?',
    'А что им написать?',
    'Что мне написать банку?',
    'Что мне работодателю написать?',
])
def test_short_request_is_recognized_or_clarified_without_inventing_context(question):
    assert is_document_request(question)
    answer=request_clarification('legal',question)
    assert 'Кому адресовано' in answer and 'Прошу' not in answer


@pytest.mark.parametrize('question',[
    'Какие документы подготовить для обращения?',
    'Я ему уже написал. Что дальше?',
    'Что ему написать на Python?',
    'Какие слова лучше написать в коде?',
])
def test_other_tasks_are_not_captured_as_short_letter_requests(question):
    assert not is_document_request(question)


def test_current_topic_overrides_previous_banking_problem_in_a_draft():
    answer=preparation_reply('legal','Напиши просьбу объяснить отказ.',previous_question='Банк списал непонятную комиссию.')
    assert 'причин отказа' in answer and 'расшифровку списания' not in answer


def test_inherited_work_context_does_not_become_a_claim_of_employment_or_an_amount():
    answer=preparation_reply('legal','А что ему конкретно написать?',previous_question='Заказчик по договору услуг не отдаёт 150000 тенге за работу.')
    assert 'Прошу оплатить причитающуюся сумму' in answer
    assert '150000' not in answer and 'пеню' not in answer and 'по трудовому договору' not in answer
    assert 'Кому: [заказчик]' in answer


def test_new_action_verb_has_the_same_bounded_meaning_in_python_and_javascript():
    for text,expected in [
        ('Начните с письменного запроса расчёта и сохраните его копию.',True),
        ('Начните с запроса, банк обязан вернуть деньги.',False),
        ('Начните с требования выплатить 99 тенге.',False),
    ]:
        assert safe_advice(text)==expected
        script='const check=new Function("text",'+json.dumps(unit_code()+'\nreturn safeAdvice(text);')+');process.stdout.write(JSON.stringify(check('+json.dumps(text)+')));'
        assert json.loads(subprocess.check_output(['node','-e',script],text=True))==expected


def test_response_planning_is_specific_to_the_current_task_and_not_a_source_of_facts():
    compare=practical_response_focus('Банк предлагает уменьшить платёж, продлив срок. На что посмотреть до согласия?')
    assert 'общую сумму выплат' in compare and 'старый и новый графики' in compare
    letter=practical_response_focus('А что ему конкретно написать?','Начальник не платит')
    assert 'редактируемый текст' in letter and 'списком действий' in letter
    definition=practical_response_focus('Что такое договор ГПХ?')
    assert 'термин простыми словами' in definition and 'инструкцию по спору' in definition
    assert '[S1]' not in compare+letter+definition


def test_format_exclusion_parser_retains_a_separate_statutory_reference():
    text=draft_task_text('Напиши запрос о расчёте, без статей и законных сроков, но отдельно проверь статью 113.')
    assert 'статью 113' in text and 'законных сроков' not in text


def test_gph_definition_search_is_not_limited_to_a_single_type_of_contract():
    from retrieval_intents import query_hints
    expansion,documents,sections=query_hints('Что такое договор ГПХ? Объясни простыми словами.')
    assert 'civil-general' in documents and 'Статья 378.' in sections['civil-general']
    assert 'gph-term-guide' in documents
    assert 'Статья 616.' in sections['civil-special'] and 'Статья 683.' in sections['civil-special']
    assert 'соглашение' in expansion and 'гражданских прав' in expansion
    _,_,specific=query_hints('Заказчик по договору подряда не платит за принятую работу')
    assert 'Статья 623.' in specific['civil-special']
    _,_,payment=query_hints('Объясни, почему заказчик по ГПХ не платит за работу')
    assert 'Статья 623.' in payment['civil-special']
    assert 'Статья 378.' not in payment.get('civil-general',[])


def test_gph_definition_retrieval_includes_the_existing_general_definition():
    from pathlib import Path
    from offline_knowledge import KnowledgeStore
    import os
    store=KnowledgeStore(os.getenv('TEST_KNOWLEDGE_DIR',str(Path(__file__).resolve().parents[2]/'offline_knowledge')))
    hits=store.search('legal','Что такое договор ГПХ? Объясни простыми словами, без длинной инструкции.',8)
    assert any(p['document_id']=='civil-general' and p.get('section','').startswith('Статья 378.') for p in hits)
    assert any(p['document_id']=='gph-term-guide' for p in hits)


def test_new_terminology_source_has_integrity_and_does_not_import_unreviewed_rights():
    from pathlib import Path
    import hashlib,os
    root=Path(os.getenv('TEST_KNOWLEDGE_DIR',str(Path(__file__).resolve().parents[2]/'offline_knowledge')))
    manifest=json.loads((root/'manifest.json').read_text())
    entry=next(d for d in manifest['documents'] if d['id']=='gph-term-guide')
    text=(root/entry['file']).read_text()
    assert hashlib.sha256(text.encode()).hexdigest()==entry['sha256']
    assert entry['document_kind']=='official_guidance' and entry['artifact_kind']=='official_web_excerpt'
    assert len(text.split())<=25
    assert 'ГПХ' in text and 'налог' not in text and 'стать' not in text
    coverage=json.loads((root/'coverage.json').read_text())
    assert coverage['documents_total']==len(manifest['documents'])


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question',['А 10% от этой суммы?','А 10 процентов от той же суммы?','Сколько будет 10% от указанной суммы?'])
def test_demonstrative_percentage_followup_uses_only_the_explicit_user_base(domain,question):
    from conversation import ConversationMemory
    memory=ConversationMemory()
    memory.resolve('!test','@synthetic',domain,'Бухгалтерия начислила 15 миллионов тенге. Сколько составят 27%?')
    search,previous=memory.resolve('!test','@synthetic',domain,question)
    result=prepare_local_reply(domain,question,NoLookup(),search_question=search,previous_question=previous)
    assert result.mechanism=='arithmetic'
    assert '1 500 000 тенге' in result.answer
    assert '15 000 000' in result.answer and 'предыдущего расчёта' in result.answer


@pytest.mark.parametrize('previous',['','У меня кредит 15 млн под 27%','Законно ли удержать 27% от 15 миллионов?'])
def test_demonstrative_followup_does_not_infer_a_base_from_legal_or_missing_context(previous):
    from arithmetic_reply import arithmetic_reply
    answer=arithmetic_reply('А 10% от этой суммы?',previous_question=previous)
    if previous:assert answer is None
    else:assert 'Укажите исходную сумму' in answer
