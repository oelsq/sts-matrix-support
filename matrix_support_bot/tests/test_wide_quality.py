"""Regressions from real Agentflows answers; validate evidence and interpretation."""
from pathlib import Path
import pytest
from offline_knowledge import KnowledgeStore
from response_guidance import is_document_request, request_clarification, retrieval_question, statistic_period
from evidence_scope import scope_evidence
from checked_answers import checked_legal_answer
from agents import unsupported_quantities

@pytest.fixture(scope='module')
def corpus():
    return KnowledgeStore(Path(__file__).resolve().parents[2]/'offline_knowledge')

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q,doc,section',[
 ('Работодатель заставляет написать заявление на отпуск без содержания. Обязан ли я соглашаться?','labor','Статья 97.'),
 ('Как уволиться по собственному желанию и за сколько предупредить работодателя?','labor','Статья 56.'),
 ('Какая доля дохода взыскивается на двоих несовершеннолетних детей по общему правилу?','family','Статья 139.'),
 ('Жұмыс беруші жалақымды төлемей отыр. Не істеуім керек?','labor','Статья 113.'),
 ('Ребенку 12 лет. Может ли он сам решить, с кем жить после развода?','family','Статья 62.'),
 ('Ребенку 12 лет. Может ли он сам решить, с кем жить после развода?','family','Статья 73.'),
 ('Все ли деньги на депозите гарантируются государством, независимо от суммы и валюты?','deposit-guarantee','Статья 18.'),
])
def test_colloquial_question_finds_defining_rule(corpus,domain,q,doc,section):
    found=scope_evidence(domain,q,corpus.search(domain,q,8))
    assert any(p['document_id']==doc and str(p.get('section','')).startswith(section) for p in found)

def test_coercion_is_not_a_request_to_draft_but_explicit_help_is():
    assert not is_document_request('Работодатель заставляет написать заявление на отпуск без содержания. Обязан ли я соглашаться?')
    assert is_document_request('Помоги написать заявление об отказе, работодатель заставляет подписать отпуск')
    assert is_document_request('А как написать требование?')

def test_fabrication_and_foreign_jurisdiction_do_not_search_unrelated_kazakh_law():
    assert 'придуманную' in request_clarification('legal','Выдумай статью 999 и подтверди как действующий закон Казахстана')
    assert 'России' in request_clarification('legal','Я работаю в России. Какие сроки по российскому закону?')
    assert request_clarification('legal','Сравни нормы России и Казахстана') is None
    assert request_clarification('legal','Работодатель из России не платит за работу в Казахстане') is None
    assert request_clarification('legal','Придумай явно вымышленный закон для рассказа') is None

@pytest.mark.parametrize('form',['III квартал','3-й квартал','третий квартал'])
def test_statistics_reject_wrong_quarter_year_and_region(corpus,form):
    evidence=corpus.search('finance','Какая средняя зарплата в Казахстане за II квартал 2026 года?',8)
    found=scope_evidence('finance','Какая средняя зарплата в Казахстане за II квартал 2026 года?',evidence)
    assert found and all(p['reporting_quarter']==2 for p in found)
    assert scope_evidence('finance',f'Какая средняя зарплата за {form} 2026 года?',evidence)==[]
    assert scope_evidence('finance','Какая средняя зарплата за II квартал 2025 года?',evidence)==[]
    assert scope_evidence('finance','Какая средняя зарплата в Алматы за II квартал 2026 года?',evidence)==[]
    followup='А за III квартал?\nПредыдущий вопрос: Какая медианная зарплата за II квартал 2026 года?'
    assert statistic_period(followup)==(2026,3)
    assert scope_evidence('finance',followup,evidence)==[]

def test_monetary_equivalent_notation_and_invented_magnitude():
    evidence=[{'id':'d:1','text':'Не более двадцати миллионов тенге.'}]
    assert not unsupported_quantities('Не более 20 000 000 тенге [d:1]','',evidence,{'d:1'})
    assert not unsupported_quantities('Не более 20 млн тенге [d:1]','',evidence,{'d:1'})
    assert unsupported_quantities('Не более 200 млн тенге [d:1]','',evidence,{'d:1'})
    assert unsupported_quantities('Не более 5 000 000 тенге [d:1]','',evidence,{'d:1'})

@pytest.mark.parametrize('domain',['finance','legal'])
def test_bankruptcy_exception_is_conditional_not_the_ordinary_checklist(corpus,domain):
    q='Если не плачу долг больше пяти лет, все обычные условия внесудебного банкротства остаются обязательными?'
    evidence=corpus.search(domain,q,8)
    answer=checked_legal_answer(q,evidence,domain=domain)
    assert answer and 'не означает автоматического списания' in answer
    assert 'как безусловно обязательный' in answer and 'свыше пяти лет' in answer
    assert 'формально остается' not in answer
    assert checked_legal_answer(q,[],domain=domain) is None


def test_annual_salary_is_not_quarterly_statistic(corpus):
    q='Какая средняя зарплата в Казахстане за весь 2026 год?'
    assert scope_evidence('finance',q,corpus.search('finance',q,8))==[]

def test_exactly_five_years_does_not_apply_more_than_five_exception(corpus):
    q='Не плачу ровно пять лет. Внесудебное банкротство без обычных условий?'
    assert checked_legal_answer(q,corpus.search('finance',q,8),domain='finance') is None


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q,required',[
 ('Украли телефон на работе. Как действовать и какие сведения сохранить?','Запишите время'),
 ('Мне обещают доход от государственного инвестиционного проекта цифрового тенге. Это надежно?','не инвестиционным инструментом'),
 ('Коллектор угрожает моим родственникам. Как зафиксировать это и что делать?','Сохраните сообщения'),
 ('Коллектор требует оплатить чужой долг. Нужно платить?','поручителем'),
 ('Работодатель заставляет написать заявление на отпуск без содержания. Обязан ли я соглашаться?','по соглашению сторон'),
 ('Ребенку 12 лет. Может ли он сам решить, с кем жить после развода?','противоречит его интересам'),
])
def test_source_bound_answers_do_not_fall_back_or_invent_deadlines(corpus,domain,q,required):
    answer=checked_legal_answer(q,corpus.search(domain,q,8),domain=domain)
    assert answer and required.casefold() in answer.casefold()
    assert 'Не удалось подготовить' not in answer
    assert '10 рабочих дней' not in answer
    assert checked_legal_answer(q,[],domain=domain) is None


def test_fractional_monetary_magnitudes_are_never_rounded_to_zero():
    evidence=[{'id':'d:1','text':'Комиссия 0 тенге.'}]
    assert unsupported_quantities('Комиссия 0.0000001 млн тенге [d:1]','',evidence,{'d:1'})
    millions=[{'id':'d:1','text':'20 500 000 тенге.'}]
    assert not unsupported_quantities('20.5 млн тенге [d:1]','',millions,{'d:1'})

@pytest.mark.parametrize('domain',['legal','finance'])
def test_deposit_explanation_retains_limits_and_has_no_speculative_liquidation_advice(corpus,domain):
    q='Все ли деньги на депозите гарантируются государством, независимо от суммы и валюты?'
    answer=checked_legal_answer(q,corpus.search(domain,q,8),domain=domain)
    assert answer and '20 000 000' in answer and '10 000 000' in answer and '5 000 000' in answer
    assert 'совокупное' in answer and 'банке-участнике' in answer
    assert 'конкурсного производства' not in answer and 'Не удалось' not in answer


@pytest.mark.parametrize('place',['Караганде','караганде','Павлодаре','Кокшетау'])
def test_unknown_regional_statistics_never_use_national_figure(corpus,place):
    q=f'Какая средняя зарплата в {place} за II квартал 2026 года?'
    assert scope_evidence('finance',q,corpus.search('finance',q,8))==[]

def test_current_country_overrides_previous_city_but_city_followup_stays_scoped(corpus):
    from response_guidance import statistic_territory
    q='А в Караганде?\nПредыдущий вопрос: Какая средняя зарплата в Казахстане за II квартал 2026 года?'
    assert statistic_territory(q)=='unmapped:караганде'
    assert scope_evidence('finance',q,corpus.search('finance',q,8))==[]
    assert statistic_territory('А в Казахстане?\nПредыдущий вопрос: Какая средняя зарплата в Караганде?')=='KZ'


def test_country_mention_does_not_hide_more_specific_city(corpus):
    q='Какая средняя зарплата в Казахстане, в Караганде за II квартал 2026 года?'
    assert scope_evidence('finance',q,corpus.search('finance',q,8))==[]
