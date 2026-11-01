import json
from pathlib import Path
import pytest
from conversation import ConversationMemory
from document_drafts import credit_request_draft, rental_refund_request, is_rental_refund_draft
from response_guidance import is_document_request
from retrieval_intents import query_hints

CASES = json.loads((Path(__file__).resolve().parents[2]/'config/content-cases-20261101.json').read_text())['cases']


@pytest.mark.parametrize('case', [c for c in CASES if c['previous_questions']], ids=lambda c:c['id'])
def test_scoped_followups_keep_the_user_topic(case):
    memory=ConversationMemory()
    for previous in case['previous_questions']:
        memory.resolve('r','alice',case['domain'],previous)
    effective, previous=memory.resolve('r','alice',case['domain'],case['question'])
    assert previous and '\nПредыдущий вопрос:' in effective
    assert case['previous_questions'][0] in effective
    assert memory.resolve('r','bob',case['domain'],case['question'])[1] == ''


@pytest.mark.parametrize('new_question', ['А как уволиться по собственному желанию?', 'Какие документы нужны по КАСКО?', 'Кому жаловаться на работодателя?'])
def test_followups_with_new_explicit_subject_reset(new_question):
    memory=ConversationMemory()
    memory.resolve('r','u','legal','Арендодатель удержал обеспечительный платёж за квартиру')
    assert memory.resolve('r','u','legal',new_question)[1] == ''


def test_make_complaint_is_a_document_request():
    assert is_document_request('Сделай претензию о возврате, сумма 200 000 тенге.')
    assert not is_document_request('Арендодатель требует подписать претензию, что делать?')
    assert is_rental_refund_draft('Сделай претензию о возврате, сумма 200 000 тенге.\nПредыдущий вопрос: Арендодатель удержал платёж за квартиру')


def test_credit_draft_copies_user_reported_values_without_guarantees():
    text=credit_request_draft('Напиши заявление. Могу платить 40 000 вместо 90 000 тенге.')
    assert 'текущий платёж составляет 90 000 тенге' in text
    assert 'посильный для меня платёж — 40 000 тенге' in text
    assert '[номер и дата договора]' in text
    assert 'обязан' not in text
    assert '[сумма]' in credit_request_draft('У меня кредит 40 000 евро, доход 90 000 тенге')
    assert '[сумма]' in credit_request_draft('Не могу платить 40 000 вместо 90 000 тенге')


def test_refund_draft_uses_only_current_explicit_tenge_amount():
    assert '200 000 тенге' in rental_refund_request('Сумма 200 000 тенге')
    assert '[сумма]' in rental_refund_request('Сумма 200 000 долларов')
    assert '[сумма]' in rental_refund_request('Сумма неизвестна\nПредыдущий вопрос: сумма 200 000 тенге')


@pytest.mark.parametrize('question,document,section', [
    ('Меня сократили, расчёт не выплатили', 'labor', 'Статья 113.'),
    ('Квартира в залоге у банка. Банк сразу становится собственником?', 'mortgage', 'Статья 20.'),
    ('По КАСКО отказали из-за исключения в полисе', 'civil-special', 'Статья 839.'),
    ('Счёт с НДС сейчас, договор подписан давно', 'tax', 'Статья 460.'),
])
def test_complex_queries_retrieve_matching_subject(question,document,section):
    _,docs,sections=query_hints(question)
    assert document in docs and section in sections[document]


def test_case_sources_exist_in_integrity_manifest():
    root=Path(__file__).resolve().parents[2]
    ids={d['id'] for d in json.loads((root/'offline_knowledge/manifest.json').read_text())['documents']}
    assert all(set(c['sources']) <= ids for c in CASES)


def test_mortgage_never_gets_rental_payment_rule_as_priority():
    _,_,sections=query_hints('Квартира в залоге у банка, один платёж просрочен')
    assert 'Статья 546.' not in sections.get('civil-special', [])


def test_draft_focus_keeps_user_amount_roles_without_legal_guarantees():
    from answer_focus import drafting_focus
    text=' '.join(drafting_focus('Напиши заявление. Могу платить 40 000 вместо 90 000 тенге.', 'Потерял работу, прошу реструктуризацию кредита', []))
    assert 'текущий платёж 90 000 тенге' in text
    assert 'предлагаемый платёж 40 000 тенге' in text


def test_future_pressure_to_resign_is_not_treated_as_completed_dismissal():
    from answer_focus import drafting_focus
    source=[dict(document_id='labor',section='Статья 56.',text='Source text')]
    future=' '.join(drafting_focus('Начальник требует подписать увольнение по собственному желанию', '', source))
    terminated=' '.join(drafting_focus('Меня сократили вчера, расчёт не выплатили', '', source))
    assert 'акт работодателя и расчёт выплат' not in future
    assert 'акт работодателя и расчёт выплат' in terminated


def test_a_requested_refund_is_not_invented_as_an_already_transferred_amount():
    text=rental_refund_request('Составь претензию о возврате. Сумма 200 000 тенге')
    assert 'передал(а) вам [сумма] [валюта платежа]' in text
    assert 'заявленную мной к возврату сумму — 200 000 тенге' in text


def test_payment_frequency_is_not_invented_from_two_reported_amounts():
    text=credit_request_draft('Могу платить 40 000 вместо 90 000 тенге')
    assert 'ежемесячн' not in text
    assert 'Периодичность платежа: [укажите по договору]' in text


@pytest.mark.parametrize('domain',['legal','finance'])
def test_editable_restructure_letter_does_not_wait_for_legal_retrieval(domain):
    from local_reply import prepare_local_reply
    class NoSearch:
        def search(self,*args,**kwargs):raise AssertionError('organisational draft must not depend on a legal snippet')
    question='Напиши заявление. Могу платить 40 000 вместо 90 000 тенге'
    previous='Потерял работу, как попросить реструктуризацию кредита?'
    reply=prepare_local_reply(domain,question,NoSearch(),search_question=question+'\nПредыдущий вопрос: '+previous,previous_question=previous)
    assert reply.mechanism=='organizational_credit_draft'
    assert '40 000' in reply.answer and '90 000' in reply.answer
    assert '[валюта платежа]' not in credit_request_draft(question)


@pytest.mark.parametrize('question',['Напиши жалобу на отказ в реструктуризации','Составь заявление о реструктуризации с законным сроком ответа','Напиши заявление о реструктуризации и рассчитай пеню'])
def test_organisational_route_does_not_replace_claims_deadlines_or_calculations(question):
    from local_reply import prepare_local_reply
    reply=prepare_local_reply('finance',question,None,allow_model_without_sources=True)
    assert reply.mechanism!='organizational_credit_draft'


def test_foreign_or_unspecified_currency_is_left_for_the_user_not_invented():
    assert '[валюта платежа]' in credit_request_draft('Могу платить 400 вместо 900 долларов')
    assert '900 тенге' not in credit_request_draft('Могу платить 400 вместо 900 долларов')
    assert '[валюта платежа]' in rental_refund_request('Сумма неизвестна')
