"""Regressions from delivered Matrix replies, with negative intent/source checks."""
import pytest
from answer_usefulness import usefulness_issues
from local_reply import prepare_local_reply
from response_guidance import is_document_request, retrieval_question
from task_preparation import preparation_reply


class NoLookup:
    def search(self,*args,**kwargs):raise AssertionError('Organisational task must not require statute retrieval')


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question,previous',[
    ('Напиши короткое письмо о выдаче расчёта задолженности.','Мне задержали оплату за два месяца работы.'),
    ('Составь обращение с просьбой дать письменный ответ.','Магазин принял претензию, но отвечает только по телефону.'),
    ('Составь запрос расчёта выплаты без утверждения, что страховщик уже нарушил закон.','По моему страховому случаю пришла меньшая сумма, чем я ожидал.'),
    ('Напиши обращение с просьбой разъяснить причину отказа.',''),
])
def test_neutral_letter_is_delivered_without_inventing_law_or_user_facts(domain,question,previous):
    p=prepare_local_reply(domain,question,NoLookup(),search_question=retrieval_question(question,previous),previous_question=previous)
    assert p.mechanism=='organizational_preparation' and p.evidence is None
    for text in ['Кому:','От:','Прошу','Дата:','Подпись:']:assert text in p.answer
    assert not usefulness_issues(question,p.answer)
    assert '[S1]' not in p.answer and 'обязан предоставить' not in p.answer
    assert '10 дней' not in p.answer and 'два месяца' not in p.answer


@pytest.mark.parametrize('question',[
    'Какие сведения подготовить для обращения за юридической помощью, если денег на юриста нет?',
    'Продавец требует оригинал паспорта в залог за рассмотрение претензии. Что подготовить без лишних персональных данных?',
    'Какие документы нужно подготовить для подачи заявления?',
    'Подготовь список документов для жалобы',
    'Начальник заставляет написать заявление об увольнении. Что делать?',
    'Я уже написал претензию. Что дальше?',
])
def test_collecting_inputs_or_reporting_a_past_letter_is_not_a_draft_request(question):
    assert not is_document_request(question)


@pytest.mark.parametrize('question',[
    'Напиши просьбу проверить запись, не обещая автоматически удалить всю историю.',
    'Как оформить просьбу исправить неверное имя в моих данных у организации?',
    'Составь нейтральную просьбу вернуть платёж или объяснить удержание.',
    'Составь запрос расчёта выплаты без утверждения, что страховщик уже нарушил закон.',
    'Кредит погасил, но в отчёте осталась просрочка. Напиши обращение без обещания удалить всю историю.',
    'Подготовь заявление о возврате денег',
])
def test_explicit_requests_include_polite_request_and_query_synonyms(question):
    assert is_document_request(question)


@pytest.mark.parametrize('question',[
    'Напиши иск о взыскании задолженности',
    'Напиши письмо и укажи обязательный срок ответа по закону',
    'Рассчитай задолженность и напиши письмо о взыскании пени',
    'Составь брачный договор',
    'Можно ли обязать продавца дать письменный ответ?',
])
def test_preparation_never_substitutes_for_a_legal_demand_or_computation(question):
    assert preparation_reply('legal',question) is None


@pytest.mark.parametrize('domain',['legal','finance'])
def test_tariff_file_freshness_is_not_misclassified_as_a_price_quote(domain):
    q='В сохранённом тарифе нет даты действия. Можно ли считать его сегодняшним?'
    p=prepare_local_reply(domain,q,NoLookup())
    assert p.mechanism=='organizational_preparation'
    assert 'дату действия' in p.answer and 'редакци' in p.answer
    assert 'Уточните банк' not in p.answer and '0 тенге' not in p.answer
    assert preparation_reply(domain,'Какая комиссия сегодня в Halyk SuperApp?') is None


@pytest.mark.parametrize('domain',['legal','finance'])
def test_loan_comparison_asks_for_comparable_inputs_without_inventing_loan_terms(domain):
    q='Мне предлагают сравнить кредиты только по ежемесячному платежу. Какие ещё сведения нужны?'
    p=prepare_local_reply(domain,q,NoLookup())
    for text in ['ГЭСВ','график','срок','расход','общей суммой выплат']:assert text in p.answer
    assert 'не учитывая один расход дважды' in p.answer
    assert preparation_reply(domain,'Сравни два кредита и посчитай точную переплату') is None


def test_school_pickup_is_personal_parental_scope_and_property_exception_survives():
    from checked_answers import checked_legal_answer
    from offline_knowledge import KnowledgeStore
    from pathlib import Path
    import os
    store=KnowledgeStore(os.getenv('TEST_KNOWLEDGE_DIR',str(Path(__file__).resolve().parents[2]/'offline_knowledge')))
    q='Нам предлагают записать в брачном договоре, кто забирает ребёнка из школы. Какие вопросы стоит разделить?'
    ev=store.search('legal',q,8)
    answer=checked_legal_answer(q,ev)
    assert 'ребёнка из школы' in answer and 'имущественном положении детей' in answer
    assert 'права и обязанности супругов' in answer
    assert checked_legal_answer(q,[]) is None
    altered=[dict(p,text=p['text'].replace('права и обязанности супругов в отношении детей','иная формулировка')) for p in ev]
    assert checked_legal_answer(q,altered) is None
    q2='Что можно определить в брачном договоре об имущественном положении детей и расходах на школу?'
    answer2=checked_legal_answer(q2,store.search('legal',q2,8))
    assert answer2 and 'допускаются условия об имущественном положении детей' in answer2

@pytest.mark.parametrize('question,previous,result',[
    ('А сколько останется после вычета?','Сколько будет 27% от 15 миллионов тенге?','10 950 000'),
    ('А сколько получится после прибавления?','Сколько будет 27% от 15000000 тенге?','19 050 000'),
    ('А после вычета сколько останется?','27% от 15000000 тенге\nА 10%?','13 500 000'),
    ('А сколько останется после вычета?','27% от 15000000 тенге\nА 10%?\nА 5%?','14 250 000'),
])
def test_followup_operation_uses_latest_explicit_user_rate(question,previous,result):
    from arithmetic_reply import arithmetic_reply
    answer=arithmetic_reply(question,previous_question=previous)
    assert result in answer and 'из ваших предыдущих' in answer


@pytest.mark.parametrize('previous',[
    '',
    '4 миллиона это 27% всей суммы',
    'Сколько будет 27% от 15000000 тенге?\nЧто делать с просроченным кредитом?',
    'Законно ли удержать 27% от 15000000 тенге?',
    '27% от 15000000 тенге\nСколько будет 100 USD плюс 20 USD?',
])
def test_followup_does_not_cross_unknown_or_legal_context(previous):
    from arithmetic_reply import arithmetic_reply
    answer=arithmetic_reply('А сколько останется после вычета?',previous_question=previous)
    assert answer and 'Уточните' in answer and '10 950 000' not in answer


def test_truthful_credit_history_is_not_promised_erased_in_a_neutral_draft():
    q='Кредит погасил, но в отчёте осталась просрочка. Напиши обращение без обещания удалить всю историю.'
    answer=preparation_reply('finance',q)
    assert answer and 'Кому:' in answer and 'Прошу' in answer and 'Подпись:' in answer
    assert 'если запись достоверна' in answer and 'необходимо удалить' not in answer


@pytest.mark.parametrize('domain',['legal','finance'])
def test_short_employer_note_after_forced_unpaid_leave_is_a_draft_without_assumed_consent(domain):
    previous='Работодатель заставляет уйти в отпуск без содержания. Обязан соглашаться?'
    q='Напиши коротко для работодателя'
    p=prepare_local_reply(domain,q,NoLookup(),search_question=retrieval_question(q,previous),previous_question=previous)
    assert p.mechanism=='organizational_preparation'
    assert 'Кому:' in p.answer and 'Прошу' in p.answer and 'Подпись:' in p.answer
    assert 'не выражает согласия' in p.answer and '[укажите свою позицию' in p.answer
    assert not usefulness_issues(q,p.answer)
    assert preparation_reply(domain,'Напиши заявление о предоставлении отпуска',previous_question=previous) is None
