"""Regressions for helpful fallback, topic retrieval and readable Matrix output."""
import json,os
from pathlib import Path
import pytest
from agents import messages,render,review_messages
from message_format import matrix_content
from offline_knowledge import KnowledgeStore
from response_guidance import practical_fallback,retrieval_question
from checked_answers import checked_legal_answer

@pytest.fixture(scope='module')
def knowledge():
    return KnowledgeStore(os.getenv('TEST_KNOWLEDGE_DIR',str(Path(__file__).resolve().parents[2]/'offline_knowledge')))

@pytest.mark.parametrize('domain,question,doc,section',[
 ('legal','Работодатель не платит зарплату. Что делать?','labor','Статья 113.'),
 ('legal','Со скольки лет можно заключить брак в Казахстане?','family','Статья 10.'),
 ('legal','Купил неисправный телефон, хочу вернуть деньги','consumer-law','Статья 30.'),
 ('legal','Украли телефон на работе','criminal','Статья 188.'),
 ('legal','Арендодатель не возвращает залог за квартиру','civil-general','Статья 338-4.'),
 ('finance','Коллекторы звонят родственникам и угрожают','collectors','Статья 5.'),
 ('finance','Можно ли оформить банкротство физического лица?','bankruptcy','Статья 5.'),
 ('finance','Какие вклады гарантирует государство?','deposit-guarantee','Статья 17.'),
 ('finance','МФО отказывает в реструктуризации микрокредита','microfinance','Статья 9-2.'),
 ('finance','Что такое ГЭСВ и как сравнить кредиты?','banks','Статья 57.'),
])
def test_everyday_question_retrieves_relevant_primary_rule(knowledge,domain,question,doc,section):
    found=knowledge.search(domain,question,8)
    assert any(p['document_id']==doc and str(p['section']).startswith(section) for p in found[:5])


def test_explicit_superapp_question_keeps_actual_communal_tariff_row(knowledge):
    from evidence_scope import scope_evidence
    question='Какая комиссия в Halyk SuperApp за оплату коммунальных услуг для зарегистрированного пользователя?'
    found=knowledge.search('finance',question,8)
    assert found and all(p['document_id']=='halyk-superapp' for p in found)
    scoped=scope_evidence('finance',question,found)
    assert scoped and 'оплата коммунальных услуг' in ' '.join(scoped[0]['text'].casefold().split())


def test_bank_change_does_not_carry_over_another_bank_tariff():
    assert retrieval_question('А какая комиссия в Freedom?', 'Комиссия Halyk SuperApp?')=='А какая комиссия в Freedom?'
    assert 'Halyk' in retrieval_question('А сколько это стоит?', 'Halyk SuperApp комиссия за оплату коммунальных услуг')
    assert retrieval_question('Украли телефон на работе','Зарплата задержана')=='Украли телефон на работе'


def test_product_definition_and_bankruptcy_exceptions_are_supplied(knowledge):
    phone=knowledge.search('legal','Купил неисправный телефон, хочу вернуть деньги',8)
    assert any('относятся исключительно транспортные средства' in ' '.join(p['text'].split()) for p in phone)
    bankruptcy=knowledge.search('finance','Можно ли оформить банкротство физического лица?',8)
    assert any('3. Несмотря на положения пункта 1' in p['text'] for p in bankruptcy)


def test_fallback_helps_without_fabricating_norms_or_refs():
    answer=practical_fallback('legal','Работодатель не платит зарплату')
    assert 'трудовой договор' in answer.casefold() and 'Что можно подготовить' in answer
    assert '!человек' not in answer and '[S1]' not in answer and 'не могу' not in answer
    assert 'организационные советы' in answer
    finance=practical_fallback('finance','Какая комиссия Kaspi?')
    assert 'название продукта' in finance and 'Какой банк' not in finance and '0 тенге' not in finance


def test_prompt_is_helpful_and_citation_rules_stay_strict():
    prompt=json.dumps(messages('legal','А если завтра?',[],previous_question='Мне задерживают зарплату'),ensure_ascii=False)
    assert 'задерживают зарплату' in prompt and 'восемью' not in prompt and 'максимум 8' not in prompt
    assert 'общие организационные советы' in prompt.casefold()
    assert 'Не подставляй нерелевантный источник' in prompt


def test_plain_and_rich_numbering_and_continued_steps():
    content=matrix_content('**Что сделать**\n\n1. Сохраните договор.\n   И переписку.\n\n1. Напишите запрос.\n\n**Уточните**\nКакая дата?')
    assert '2. Напишите запрос.' in content['body']
    assert '<li>Сохраните договор.<br>И переписку.</li>' in content['formatted_body']
    assert content['formatted_body'].count('<ol>')==1


def test_markdown_links_are_safe_and_readable():
    rich=matrix_content('[Официальный источник](https://example.org/law?a=1&b=2)')['formatted_body']
    assert '<a href="https://example.org/law?a=1&amp;b=2">Официальный источник</a>' in rich
    unsafe=matrix_content('[ссылка](javascript:alert)')['formatted_body']
    assert 'href="javascript:' not in unsafe


def test_template_placeholders_are_not_unknown_citations():
    evidence=[{'id':'law:p1:1:1','title':'Закон','text':'Потребитель вправе обратиться с претензией.',
               'page':1,'reviewed_on':'2026-10-07','source':'https://example.org/law'}]
    good=render('legal','Можно обратиться с претензией. [S1]\n\n**Черновик**\nДата: [дата]. Заявитель: [имя].','Составь претензию',evidence)
    assert 'Не удалось' not in good and '[дата]' in good
    assert 'Не удалось' in render('legal','Право [S1] и [S9]','',evidence)


def test_invented_mrp_conversion_is_rejected_but_known_threshold_survives():
    evidence=[{'id':'law:p1:1:1','title':'Банкротство','text':'Обязательства, не превышающие 1600 МРП.',
               'page':1,'reviewed_on':'2026-10-07','source':'https://example.org/law'}]
    assert 'Не удалось' in render('finance','Порог 1600 МРП, в 2026 году МРП равен 3450 тенге. [S1]','Условия банкротства?',evidence)
    assert 'Не удалось' not in render('finance','Один из критериев — обязательства не более 1600 МРП. [S1]','Условия банкротства?',evidence)


def test_spelled_out_source_period_is_not_mistaken_for_an_invented_number():
    evidence=[{'id':'law:p1:1:1','title':'Закон','text':'В течение семи лет и двенадцати месяцев.',
               'page':1,'reviewed_on':'2026-10-07','source':'https://example.org/law'}]
    answer=render('finance','Периоды: 7 лет и 12 месяцев. [S1]','',evidence)
    assert 'Не удалось' not in answer


def test_review_receives_the_real_evidence_and_marks_draft_as_untrusted():
    result=review_messages('legal','Срок ответа?', 'Выдуманный срок 99 дней',[])
    assert '99 дней' in result[-1]['content'] and 'не источник' in result[-1]['content']
    assert 'не подставляй название ведомства' in result[0]['content']


def test_phone_answer_uses_closed_definition_and_does_not_require_substantial_defect(knowledge):
    q='Купил неисправный телефон, хочу вернуть деньги'
    ev=knowledge.search('legal',q,8)
    a=checked_legal_answer(q,ev)
    assert a and 'телефон не включён в закрытый перечень' in a
    assert 'возврат денег' in a and 'Неподтверждён' not in a and 'Не удалось' not in a
    assert checked_legal_answer(q,[]) is None


def test_rental_answer_is_conditional_and_does_not_promise_automatic_refund(knowledge):
    q='Арендодатель не возвращает залог за квартиру'
    a=checked_legal_answer(q,knowledge.search('legal',q,8))
    assert a and 'Если платёж юридически является гарантийным взносом' in a
    assert 'Возврат гарантирован' not in a and 'Это условное правило' in a
    assert 'Не удалось' not in a and checked_legal_answer(q,[]) is None

@pytest.mark.parametrize('question,doc,section',[
 ('Со скольки лет могут забирать в армию военкомат?', 'military-service', 'Статья 31.'),
 ('про КНБ что есть?', 'national-security', 'Статья 2.'),
])
def test_military_and_security_everyday_terms(knowledge, question, doc, section):
    found = knowledge.search('legal', question, 8)
    assert any(p['document_id'] == doc and str(p['section']).startswith(section) for p in found[:4])


def test_missing_reference_is_not_treated_as_a_dispute():
    answer = practical_fallback('legal', 'Что такое нотариат?')
    assert 'нотариат' in answer and 'не найден источник' in answer
    assert 'Сохраните' not in answer and 'Что можно подготовить' not in answer
    rejected = practical_fallback('legal', 'Что такое нотариат?', validation_failed=True)
    assert 'не прошёл проверку' in rejected and 'не найден источник' not in rejected


def test_age_written_in_words_is_not_rejected_as_invented():
    from agents import unsupported_quantities
    ev = [{'id':'military:p1:1:1','text':'от восемнадцати до двадцати семи лет'}]
    assert not unsupported_quantities('От 18 до 27 лет. [S1]', '', ev, {'military:p1:1:1'})
    assert unsupported_quantities('До 28 лет. [S1]', '', ev, {'military:p1:1:1'})


def test_reference_question_is_not_expanded_into_action_plan():
    from response_guidance import is_reference_question
    assert is_reference_question('Со скольки лет могут забирать в армию военкомат?')
    assert is_reference_question('про КНБ что есть?')
    assert not is_reference_question('Мне угрожают коллекторы, что делать?')
    prompt = json.dumps(review_messages('legal','Со скольки лет призывают?', 'Черновик', []),ensure_ascii=False)
    assert 'до 80 слов без плана действий' in prompt


def test_conscription_age_is_short_and_bound_to_actual_clause(knowledge):
    q='Со скольки лет могут забирать в армию военкомат?'
    ev=knowledge.search('legal',q,8)
    answer=checked_legal_answer(q,ev)
    assert answer and 'с 18 лет' in answer and 'до 27 лет' in answer
    assert 'Что сделать сейчас' not in answer and 'отсрочк' in answer
    assert checked_legal_answer(q,[]) is None


@pytest.mark.parametrize('effective_date', ['15 сентября 2026 года', '15.09.2026', '2026-09-15'])
def test_tariff_effective_date_is_supported_by_explicit_version(effective_date):
    evidence=[{'id':'tariff:1','title':'Halyk: тарифы SuperApp','text':'Оплата коммунальных услуг 0 теңге',
               'page':1,'reviewed_on':'2026-10-01','version':'Изменения, действующие с 15.09.2026.',
               'source':'https://example.org/tariff.pdf'}]
    answer=f'Комиссия Halyk SuperApp составляет 0 тенге. Условия действуют с {effective_date}. [S1]'
    assert 'Не удалось' not in render('finance',answer,'Какая комиссия в Halyk SuperApp?',evidence)


@pytest.mark.parametrize('claim', ['с 16 сентября 2026 года', 'с 01.10.2026', 'с 2026-10-01',
                                   'с 31 сентября 2026 года', 'комиссия 2026 тенге'])
def test_effective_date_metadata_cannot_support_wrong_dates_or_fees(claim):
    evidence=[{'id':'tariff:1','title':'Тариф','text':'Комиссия 0 теңге',
               'reviewed_on':'2026-10-01','version':'Изменения, действующие с 15.09.2026.'}]
    assert 'Не удалось' in render('finance',f'Условия {claim}. [S1]','Какая комиссия?',evidence)


def test_uncited_version_and_retrieval_date_are_not_effective_date_proof():
    evidence=[{'id':'tariff:1','title':'Тариф','text':'Комиссия 0 теңге','reviewed_on':'2026-10-01',
               'version':'Документ получен 2026-10-01'},
              {'id':'tariff:2','title':'Другой тариф','text':'Другая услуга 0 теңге',
               'version':'Изменения, действующие с 15.09.2026.'}]
    for date_text in ['15 сентября 2026 года', '01.10.2026']:
        assert 'Не удалось' in render('finance',f'Комиссия 0 тенге с {date_text}. [S1]','Какая комиссия?',evidence)


def test_tariff_validation_failure_does_not_claim_missing_documents_or_repeat_product_question():
    answer=practical_fallback('finance','Какая комиссия в Halyk SuperApp?',validation_failed=True)
    assert 'Документ по вашему вопросу найден' in answer
    assert 'Что можно подготовить' not in answer and 'Уточните название продукта' not in answer


@pytest.mark.parametrize('case', json.loads((Path(__file__).resolve().parents[2] / 'config' / 'answer-evaluation.json').read_text())['cases'], ids=lambda c:c['id'])
def test_control_question_retrieval(knowledge, case):
    from evidence_scope import scope_evidence
    from conversation import ConversationMemory
    memory = ConversationMemory()
    for previous in case.get('previous_questions', []):
        memory.resolve('room', 'user', case['domain'], previous)
    question, _ = memory.resolve('room', 'user', case['domain'], case['question'])
    found = scope_evidence(case['domain'], question, knowledge.search(case['domain'], question,8))
    if case.get('expect_empty'):
        assert found == []
    else:
        assert any(p['document_id'] == case['document'] and
                   (not case.get('section') or str(p.get('section','')).startswith(case['section']))
                   for p in found[:case.get('top_k',5)]), case['id']


def test_checked_fact_does_not_override_new_followup_intent(knowledge):
    q = 'Со скольки лет могут забирать в армию военкомат?'
    evidence = knowledge.search('legal',q,8)
    assert checked_legal_answer('А как подать жалобу?\nПредыдущий вопрос: '+q,evidence) is None
    q = 'Купил неисправный телефон, хочу вернуть деньги'
    evidence = knowledge.search('legal',q,8)
    assert checked_legal_answer('А составь претензию?\nПредыдущий вопрос: '+q,evidence) is None


def test_finance_room_keeps_wage_topic_when_requesting_an_actual_letter(knowledge):
    from conversation import ConversationMemory
    from evidence_scope import scope_evidence
    from response_guidance import is_document_request
    memory = ConversationMemory()
    first = 'Начальник не отдаёт деньги за работу'
    memory.resolve('finance-room', 'worker', 'finance', first)
    current = 'А как написать требование?'
    query, context = memory.resolve('finance-room', 'worker', 'finance', current)
    assert context == first and first in query and is_document_request(current)
    evidence = scope_evidence('finance', query, knowledge.search('finance', query, 8))
    assert any(p['document_id']=='labor' and p['section'].startswith('Статья 113.') for p in evidence[:2])
    prompt = messages('finance', current, evidence, context)
    assert first in prompt[-1]['content'] and 'готовый редактируемый черновик' in prompt[0]['content']


def test_actual_bot_callback_preserves_finance_conversation_and_draft(knowledge, monkeypatch, tmp_path):
    # Exercise the production callback twice. No Matrix/LLM network requests.
    import asyncio, time
    from types import SimpleNamespace
    import bot
    from flowise_client import FlowiseClient
    outgoing, prompts = [], []
    room_id = '!finance-pilot:demo.local'
    class Flow:
        routes = {room_id: {'domain':'finance'}}
        def selected(self, room, domain):
            return room == room_id and domain == 'finance'
        async def predict(self, http, domain, room, sender, event, msgs):
            prompts.append(msgs)
            if event == '$letter':
                return ('При трудовых отношениях зарплата выплачивается работодателем. [S1]\n\n'
                        '**Черновик**\nРаботодателю [организация]\nОт [имя]\n'
                        'Прошу выплатить задолженность за [период] в размере [сумма].\nДата: [дата].')
            return 'При трудовых отношениях зарплата выплачивается работодателем. [S1]'
    class Login: pass
    class Client:
        user = '@bot:demo.local'
        def __init__(self, *args, **kwargs): self.callback = None
        async def login(self, *args, **kwargs): return Login()
        def add_event_callback(self, callback, event_class):
            if event_class == bot.RoomMessageText: self.callback = callback
        async def sync_forever(self, **kwargs):
            for identifier, text in [('$wages','Начальник не отдаёт деньги за работу'),
                                     ('$letter','А как написать требование?')]:
                await self.callback(SimpleNamespace(room_id=room_id), SimpleNamespace(
                    sender='@worker:demo.local',server_timestamp=int(time.time()*1000)+1000,
                    event_id=identifier,body=text))
        async def room_send(self, **kwargs): outgoing.append(kwargs['content'])
        async def close(self): pass
    monkeypatch.setattr(bot, 'AsyncClient', Client)
    monkeypatch.setattr(bot, 'LoginResponse', Login)
    monkeypatch.setattr(bot, 'KnowledgeStore', lambda _: knowledge)
    monkeypatch.setattr(bot, 'department_roster', lambda _: {})
    monkeypatch.setattr(bot, 'read_llm_key', lambda: 'synthetic-key')
    monkeypatch.setattr(FlowiseClient, 'from_env', lambda _: Flow())
    for name, value in {'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:demo.local',
                        'MATRIX_PASSWORD':'synthetic-password','LLM_ENDPOINT':'http://litellm:4000/v1',
                        'LLM_MODEL':'synthetic-model','KNOWLEDGE_DIR':'synthetic',
                        'MATRIX_ROOM_ROUTES_FILE':str(tmp_path/'no-routes'),'OFFLINE_MODE':'0'}.items():
        monkeypatch.setenv(name,value)
    asyncio.run(bot.main())
    answers = [p['body'] for p in outgoing if p['body'] != 'Готовлю ответ по проверенным документам…']
    assert len(answers)==2 and prompts==[]  # These two intents use verified source templates.
    assert all('Трудовой кодекс' in a for a in answers)
    assert 'Черновик' in answers[1] and '[сумма]' in answers[1]
    assert not any('Не удалось' in p or 'временно недоступен' in p for p in answers)


@pytest.mark.parametrize('domain', ['legal','finance'])
def test_wage_guide_and_letter_are_conditional_source_bound_and_useful(knowledge, domain):
    from checked_answers import checked_work_payment_answer
    from evidence_scope import scope_evidence
    q='Начальник не отдаёт деньги за работу'
    ev=scope_evidence(domain,q,knowledge.search(domain,q,8))
    first=checked_work_payment_answer(domain,q,ev)
    assert first and 'Если это зарплата по трудовым отношениям' in first
    assert 'нет норм' not in first and 'Не удалось' not in first
    followup='А как написать требование?\nПредыдущий вопрос: '+q
    letter=checked_work_payment_answer(domain,followup,ev)
    assert letter and all(t in letter for t in ['Черновик','[сумма]','[период]','Прошу проверить расчёт'])
    assert all(t not in letter for t in ['если в договоре не указано иное','3 рабочих дня','обратиться в суд'])
    assert checked_work_payment_answer(domain,followup,[]) is None
    assert checked_work_payment_answer(domain,'А если ГПХ?\nПредыдущий вопрос: '+q,ev) is None
    assert checked_work_payment_answer(domain,'А сколько пени?\nПредыдущий вопрос: '+q,ev) is None
    assert checked_work_payment_answer(domain,'А как написать жалобу на увольнение?\nПредыдущий вопрос: '+q,ev) is None
    # A document title or mere mention of wages is not evidence of the rules.
    fake=[{**p,'text':'В документе упоминается заработная плата.'} for p in ev]
    assert checked_work_payment_answer(domain,q,fake) is None


@pytest.mark.parametrize('validation_failed', [False, True])
def test_letter_request_still_gets_an_editable_example_if_sources_or_validation_fail(validation_failed):
    question='А как написать требование?\nПредыдущий вопрос: Начальник не отдаёт деньги за работу'
    answer=practical_fallback('finance',question,validation_failed=validation_failed)
    assert all(t in answer for t in ['Черновик','[период]','[сумма]','Прошу проверить расчёт'])
    assert not any(t in answer for t in ['[S1]','нет норм','3 рабочих дня','обратиться в суд'])
