import pytest
from bot_help import help_reply,is_help_request
from conversation_intake import intake_reply
from local_reply import prepare_local_reply


class NoSearch:
    def search(self,*args,**kwargs):raise AssertionError('Missing inputs and service help must not search for laws')


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question',['привет','Здравствуйте!','Добрый день','Салам','Сәлем!','Hi','привет, бот','спасибо','Спасибо большое!','Благодарю вас'])
def test_social_messages_are_fast_service_messages(domain,question):
    result=prepare_local_reply(domain,question,NoSearch(),allow_model_without_sources=True)
    assert result.mechanism=='service_help' and result.answer
    assert 'провер' not in result.answer and 'источник' not in result.answer and '[S' not in result.answer


@pytest.mark.parametrize('question',['На какие вопросы ты можешь отвечать?','На какие вопросы вы можете ответить?','Подскажи, на какие запросы ты умеешь отвечать','На какие вопросы можешь отвечать?'])
def test_capability_paraphrases_use_product_information(question):
    assert is_help_request(question)
    for domain,expected in [('legal','юридическ'),('finance','кредит')]:
        answer=help_reply(question,domain)
        assert expected in answer.lower() and '!человек' in answer and '!сотрудники' in answer


@pytest.mark.parametrize('question',['Привет, меня увольняют. Что делать?','Здравствуйте, сколько будет 27% от 15 миллионов?','Спасибо, а как написать жалобу?','На какие вопросы обязан отвечать работодатель?','На какие вопросы ты можешь отвечать по уголовному делу?'])
def test_service_patterns_do_not_swallow_real_tasks(question):
    assert help_reply(question,'legal') is None


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question',['сколько выходит моя зарплата?','Какая у меня зарплата?','Сколько получу зарплаты?','Помоги рассчитать зарплату','Как посчитать мою зп?'])
def test_salary_without_inputs_asks_for_amount_period_and_net_scope(domain,question):
    reply=prepare_local_reply(domain,question,NoSearch(),allow_model_without_sources=True)
    assert reply.mechanism=='salary_intake'
    assert 'оклад' in reply.answer and 'период' in reply.answer and 'на руки' in reply.answer
    assert 'увольнен' not in reply.answer and 'задержк' not in reply.answer and '[S' not in reply.answer


@pytest.mark.parametrize('question',['А какие данные тебе нужны?','Какие сведения вам нужны?','На руки','чистыми'])
def test_salary_intake_continues_from_user_context(question):
    reply=intake_reply('finance',question,'сколько выходит моя зарплата?')
    assert reply and reply.mechanism=='salary_intake'


@pytest.mark.parametrize('question,expected',[
 ('Начислено 300000 тенге, удержано всего 42000. Сколько получу на руки?','258 000 тенге'),
 ('Начислено: 300 000 тг. Удержания: 42 000 тг. Сколько на руки?','258 000 тенге'),
 ('Начислено 300 тыс тенге, удержали 42 тыс тенге, сколько получу?','258 000 тенге'),
 ('Начислено 1500,50 тенге, удержано 100,25 тенге. Сколько получу на руки?','1 400,25 тенге'),
])
def test_salary_net_uses_only_explicit_gross_and_total_deductions(question,expected):
    result=intake_reply('finance',question)
    assert result.mechanism=='salary_arithmetic' and expected in result.answer
    assert '%' not in result.answer and 'правильность' in result.answer


@pytest.mark.parametrize('question',[
 'Сколько составляет средняя зарплата в Казахстане?',
 'Законно ли удержали 42000 из зарплаты?',
 'Работодатель не платит зарплату. Как взыскать?',
 'Какие данные нужны для расчёта зарплаты на руки?',
 'Оклад 300000, отработал 15 из 20 дней. Сколько получу?',
 'Зарплата 300000, какой налог по закону?',
])
def test_intake_does_not_replace_statistical_legal_or_fully_specified_tasks(question):
    assert intake_reply('finance',question) is None


@pytest.mark.parametrize('question',[
 'Сотрудник попал в больницу из за драки',
 'Работника госпитализировали после избиения',
 'Коллегу избили, у него травмы. Что сейчас делать?',
])
def test_incident_intake_preserves_uncertainty_and_specialist_focus(question):
    law=prepare_local_reply('legal',question,NoSearch(),allow_model_without_sources=True)
    money=prepare_local_reply('finance',question,NoSearch(),allow_model_without_sources=True)
    assert law.mechanism==money.mechanism=='incident_intake'
    assert 'медицинск' in law.answer and 'полици' in law.answer and 'на работе' in law.answer
    assert 'выплат' in money.answer and 'расч' in money.answer
    assert 'не прошёл проверку' not in law.answer and '[S' not in law.answer
    assert 'стать' not in money.answer and 'уволить' not in law.answer


@pytest.mark.parametrize('question',[
 'Сотрудник попал в больницу из-за драки. Можно ли уволить?',
 'Сотрудник госпитализирован после драки. Кто обязан оплатить больничный?',
 'Какая уголовная ответственность, если человека избили и он в больнице?',
 'Покрывает ли страховка травмы после драки?',
 'Какой срок подачи заявления после избиения?',
])
def test_substantive_incident_law_goes_to_source_grounded_path(question):
    assert intake_reply('legal',question) is None


def test_real_validation_failure_has_specific_department_without_fake_transfer():
    from response_guidance import practical_fallback
    for domain,command in [('legal','!юрдеп'),('finance','!финдеп')]:
        answer=practical_fallback(domain,'Нужна индивидуальная проверка непонятного условия',validation_failed=True)
        assert command in answer and '!человек' in answer
        assert 'по вашей команде' in answer and 'уже передан' not in answer
        assert 'не прошёл проверку подтверждений' not in answer
        assert 'пришлите' not in answer.lower() or 'текст нормы' not in answer.lower()


def test_greeting_and_intake_are_not_validation_failures_or_department_escalations():
    from response_guidance import practical_fallback
    for domain in ['legal','finance']:
        for question in ['привет','На какие вопросы ты можешь отвечать?','сколько выходит моя зарплата?','Сотрудник попал в больницу из за драки']:
            answer=practical_fallback(domain,question,validation_failed=True)
            assert 'не прошёл' not in answer and 'не удалось надёжно' not in answer.lower()
            assert 'по вашей команде' not in answer


def test_incident_followup_does_not_reask_location_and_role_already_supplied():
    result=intake_reply('legal','Его избили после работы. Какие первые действия?','Я работодатель, сотрудник в больнице после драки')
    assert result and result.mechanism=='incident_intake'
    assert 'Вы обращаетесь как' not in result.answer
    assert 'Драка произошла на работе или' not in result.answer
    assert 'полици' in result.answer


def test_transport_failure_offers_support_but_successful_answer_does_not():
    from answer_usefulness import department_note
    assert '!человек' in department_note('Поясни условия договора','Сервис генерации временно недоступен; выше — ограниченная помощь.')
    assert not department_note('Поясни условия договора','Проверьте предмет договора, оплату и основания расторжения.')


def test_negative_sign_is_not_silently_changed_into_positive_salary():
    result=intake_reply('finance','Начислено -300000 тенге, удержано 42000. Сколько на руки?')
    assert result is None or result.mechanism!='salary_arithmetic'


@pytest.mark.parametrize('question', [
 'Начислено 300000 тенге, удержано 42000 и аванс 100000. Сколько на руки?',
 'Начислено 300000 тенге, удержано 42000, ещё премия 50000. Сколько получу?',
])
def test_salary_does_not_ignore_additional_amounts(question):
    result=intake_reply('finance',question)
    assert result is None or result.mechanism != 'salary_arithmetic'


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question', [
 'Добрый вечер! Помощь нужна.', 'Привет, мне нужна помощь',
 'Здравствуйте, можете мне помочь, пожалуйста?', 'Салам, помоги',
])
def test_greeting_with_generic_help_does_not_enter_legal_retrieval(domain, question):
    reply=prepare_local_reply(domain,question,NoSearch(),allow_model_without_sources=True)
    assert reply.mechanism == 'service_help'
    assert '!помощь' in reply.answer and '!человек' in reply.answer


@pytest.mark.parametrize('question',[
 'Добрый вечер! Помощь нужна с увольнением',
 'Привет, мне нужна помощь: банк удержал комиссию',
 'Здравствуйте, можете мне помочь написать заявление?',
])
def test_greeting_does_not_hide_the_actual_task(question):
    assert help_reply(question,'legal') is None
