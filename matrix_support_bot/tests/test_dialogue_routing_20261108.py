"""Regressions from the November 8 synthetic dialogue review."""
from pathlib import Path

import pytest

from arithmetic_reply import arithmetic_reply
from bot_help import is_help_request
from conversation import ConversationMemory
from local_reply import prepare_local_reply
from offline_knowledge import KnowledgeStore
from response_guidance import work_payment_question


@pytest.fixture(scope='module')
def knowledge():
    return KnowledgeStore(Path(__file__).parents[2] / 'offline_knowledge')


@pytest.mark.parametrize('question', [
    'А теперь другой вопрос: хозяин съёмной квартиры удержал депозит. Какие документы собрать для проверки?',
    'Новый вопрос: какие документы нужны по удержанию арендного депозита?',
])
def test_explicit_new_topic_resets_mortgage_context(knowledge, question):
    memory = ConversationMemory()
    memory.resolve('room', 'user', 'legal', 'Квартира в ипотеке. Банк прислал уведомление о переходе собственности.')
    search, previous = memory.resolve('room', 'user', 'legal', question)
    assert search == question and previous == ''
    reply = prepare_local_reply('legal', question, knowledge, search_question=search, previous_question=previous)
    assert reply.mechanism == 'organizational_preparation'
    assert 'Договор аренды' in reply.answer and 'Акты передачи' in reply.answer


def test_real_followup_still_retains_context():
    memory = ConversationMemory()
    original = 'Договор аренды закончился, собственник удержал депозит.'
    memory.resolve('room', 'user', 'legal', original)
    _, previous = memory.resolve('room', 'user', 'legal', 'А какие документы собрать?')
    assert previous == original


@pytest.mark.parametrize('question, expected', [
    ('А после вычета уже 12% сколько останется от исходной суммы?', '2 112 000 тенге'),
    ('Прибавь к исходной сумме 5%, сколько получится?', '2 520 000 тенге'),
    ('Вычти из исходной суммы 5%, сколько останется?', '2 280 000 тенге'),
    ('После вычета 0% сколько останется от первоначальной суммы?', '2 400 000 тенге'),
])
@pytest.mark.parametrize('domain', ['finance', 'legal'])
def test_explicit_original_base_arithmetic_uses_shared_entry_point(question, expected, domain):
    class NoSearch:
        def search(self, *args, **kwargs):
            raise AssertionError('Explicit arithmetic must not require legal search')

    memory = ConversationMemory()
    memory.resolve('room', 'user', domain, 'Сколько составят 17,5% от 2,4 миллиона тенге?')
    search, previous = memory.resolve('room', 'user', domain, question)
    reply = prepare_local_reply(domain, question, NoSearch(), search_question=search, previous_question=previous)
    assert reply.mechanism == 'arithmetic' and expected in reply.answer


@pytest.mark.parametrize('question, previous', [
    ('Прибавь к исходной сумме 5%, сколько получится?', 'Какой налог с дохода 2400000 тенге?'),
    ('Прибавь к исходной сумме 5%, сколько получится?', 'У меня кредит 2400000 тенге под 17,5% годовых.'),
    ('Не прибавляй к исходной сумме 5%, объясни договор.', '17,5% от 2400000 тенге'),
    ('Прибавь к исходной сумме 5% и 10%, сколько получится?', '17,5% от 2400000 тенге'),
])
def test_new_arithmetic_form_never_infers_a_legal_or_ambiguous_base(question, previous):
    answer = arithmetic_reply(question, previous)
    assert answer is None or 'Уточните исходную сумму' in answer


@pytest.mark.parametrize('domain', ['legal', 'finance'])
def test_staff_switch_question_gets_implemented_commands(domain):
    class NoSearch:
        def search(self, *args, **kwargs):
            raise AssertionError('Bot help needs no legal sources')

    question = 'Если мне не понравился сотрудник юрдепа, как выбрать другого и какие команды у тебя есть?'
    reply = prepare_local_reply(domain, question, NoSearch())
    assert reply.mechanism == 'service_help'
    assert '!сотрудники' in reply.answer and '!другой' in reply.answer


@pytest.mark.parametrize('question', [
    'Сотрудник незаконно удерживает мою зарплату. Как выбрать способ защиты?',
    'Мне не понравился сотрудник банка. Может ли он отказать в выдаче вклада?',
])
def test_actual_legal_case_is_not_swallowed_by_help(question):
    assert not is_help_request(question)


@pytest.mark.parametrize('question', [
    'На карту пришло 900000 тенге: часть за работу, часть вернул друг как долг. Можно считать всё одной зарплатой и удержать одинаковый налог?',
    'Какие налоги с зарплаты и возвращённого другом долга?',
])
def test_tax_classification_does_not_get_unpaid_wages_template(knowledge, question):
    assert not work_payment_question(question)
    reply = prepare_local_reply('finance', question, knowledge, allow_model_without_sources=True)
    assert reply.mechanism != 'source_bound_work'
    if 'На карту' in question:
        assert any(p['document_id'] == 'tax' and p.get('section', '').startswith('Статья 371.') for p in reply.evidence)


def test_real_unpaid_salary_question_keeps_work_payment_route():
    assert work_payment_question('Работодатель не платит зарплату. Как повлияет долг на мои налоги?')


def test_missing_paper_contract_retrieves_the_formation_rule(knowledge):
    question = 'Три недели работал в кафе, деньги не заплатили. Бумажного договора нет, есть переписка и график смен. Получается, требовать оплату бесполезно?'
    evidence = prepare_local_reply('legal', question, knowledge, allow_model_without_sources=True).evidence
    assert any(p['document_id'] == 'labor' and p.get('section', '').startswith('Статья 33.') for p in evidence)
    assert any(p['document_id'] == 'labor' and p.get('section', '').startswith('Статья 113.') for p in evidence)


def test_author_permission_question_retains_both_distinct_rights(knowledge):
    question = 'Фотограф разрешил мне использовать снимок, но на фото узнаваемый другой человек. Разрешения автора достаточно для любой публикации?'
    evidence = knowledge.search('legal', question, 8)
    for document, section in [('copyright', 'Статья 16.'), ('civil-general', 'Статья 145.')]:
        assert any(p['document_id'] == document and p.get('section', '').startswith(section) for p in evidence)


def test_personal_archive_biometric_comparison_keeps_the_scope_exception(knowledge):
    question = 'Я обычным телефоном снимаю прогулку в парке для семейного архива. Это уже биометрическая идентификация прохожих или нужно различать эти ситуации?'
    evidence = knowledge.search('legal', question, 8)
    assert any(p['document_id'] == 'personal-law' and p.get('section', '').startswith('Статья 3.') and 'личных' in p['text'] for p in evidence)


def test_family_recording_comparison_requires_explanation_and_identity_source(knowledge):
    from answer_focus import practical_response_focus
    from response_goal import response_goal
    question = 'Я обычным телефоном снимаю прогулку в парке для семейного архива. Это уже биометрическая идентификация прохожих или нужно различать эти ситуации?'
    assert response_goal(question)['kind'] == 'comparison'
    assert 'Сначала сопоставь' in practical_response_focus(question)
    evidence = knowledge.search('legal', question, 8)
    assert any(p['document_id'] == 'personal-law' and p.get('section', '').startswith('Статья 6.') and 'идентификации' in p['text'] for p in evidence)


@pytest.mark.parametrize('domain', ['legal', 'finance'])
@pytest.mark.parametrize('question', [
    'На карту пришло 900000 тенге: часть за работу, часть вернул друг как долг. Можно считать всё одной зарплатой и удержать одинаковый налог?',
    'Мне перечислили оплату услуги, а отдельным переводом вернули личный долг. Какие факты нужны, чтобы не смешать виды поступлений при проверке налога?',
])
def test_receipt_categories_answer_requires_both_actual_clauses(knowledge, domain, question):
    reply = prepare_local_reply(domain, question, knowledge, allow_model_without_sources=True)
    assert reply.mechanism == 'source_bound_income_categories'
    assert 'нельзя автоматически' in reply.answer and 'виды доходов' in reply.answer
    assert 'договор' in reply.answer and 'первоначальной передачи' in reply.answer
    assert 'ещё не установлены' in reply.answer and '!человек' not in reply.answer
    from checked_finance_terms import checked_receipt_classification
    assert checked_receipt_classification(domain, question, []) is None
    altered = [{**p, 'text': p['text'].replace('в связи с наличием трудовых отношений', 'по другим основаниям')} for p in reply.evidence]
    assert checked_receipt_classification(domain, question, altered) is None


@pytest.mark.parametrize('question', [
    'На карту поступила оплата услуги и возврат долга. Сколько точно налога с каждой суммы?',
    'Напиши декларацию по налогу с разных поступлений на карту: зарплата и долг.',
    'Какой срок подачи декларации по налогу с разных поступлений на карту?',
    'Какой корпоративный налог с разных переводов за услуги и возврата долга?',
])
def test_category_explanation_does_not_replace_tax_calculation_or_filing(knowledge, question):
    from checked_finance_terms import checked_receipt_classification
    evidence = knowledge.search('finance', question, 8)
    assert checked_receipt_classification('finance', question, evidence) is None


@pytest.mark.parametrize('question', [
    'У меня договор оказания услуг как у самозанятого. Это всё равно задолженность по зарплате?',
    'Заказчик не оплатил работу по договору подряда. Трудового договора у нас нет. Нужно ли требовать именно зарплату или учитывать вид договора?',
])
def test_civil_comparison_retains_distinction_without_wage_procedure(knowledge, question):
    from response_goal import response_goal
    reply = prepare_local_reply('legal', question, knowledge, allow_model_without_sources=True)
    assert response_goal(question)['requires_explanation']
    assert any(p['document_id'] == 'labor' and p.get('section', '').startswith('Статья 27.') for p in reply.evidence)
    assert not any(p['document_id'] == 'labor' and not p.get('section', '').startswith('Статья 27.') for p in reply.evidence)
    assert reply.mechanism != 'source_bound_work'


def test_literal_identification_paraphrase_uses_the_same_evidence_scope(knowledge):
    from retrieval_intents import biometric_image_question
    from answer_focus import practical_response_focus
    question = 'Снимаю прогулку в парке на обычный телефон для семейного альбома. Чем такая запись отличается от обработки лица для установления личности?'
    assert biometric_image_question(question)
    assert 'Сначала сопоставь' in practical_response_focus(question)
    evidence = knowledge.search('legal', question, 8)
    assert any(p['document_id'] == 'personal-law' and p.get('section', '').startswith('Статья 6.') for p in evidence)


def test_civil_collection_case_keeps_its_existing_procedure_boundary():
    from retrieval_intents import civil_employment_comparison
    assert not civil_employment_comparison('Договор ГПХ. Напиши иск о взыскании оплаты и зарплаты.')


def test_personal_video_comparison_keeps_public_identification_conditions(knowledge):
    from answer_focus import practical_response_focus
    question = 'Снимаю прогулку в парке на обычный телефон для семейного альбома. Чем такая запись отличается от обработки лица для установления личности?'
    evidence = knowledge.search('legal', question, 8)
    assert any(p['document_id'] == 'personal-law' and p.get('section', '').startswith('Статья 7.') and 'технологических средств' in p['text'] for p in evidence)
    focus = practical_response_focus(question)
    assert 'сама по себе не определяет вид данных' in focus
    assert 'Не выводи определение биометрических данных из статьи о сфере' in focus


def test_paid_posing_exception_needs_a_fact_about_depicted_person():
    from answer_focus import practical_response_focus
    question = 'Автор фотоснимка дал разрешение на публикацию. На снимке узнаваем незнакомый человек. Разрешение автора снимает вопрос согласия изображённого лица?'
    focus = practical_response_focus(question)
    assert 'к самому изображённому лицу' in focus
    assert 'без такого факта' in focus
