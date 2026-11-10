import asyncio,json,copy
from datetime import date
from decimal import Decimal
import pytest
from cashflow_calculator import calculate,calculator_reply,CalculationInputError,TEMPLATE
from evidence_research import research_evidence
from case_facts import case_facts
from source_applicability import applicable,tariff_review_due
from review_result import ReviewedAnswer,ReviewDetails

DATA={'amount_received':'95000','received_on':'2026-10-01','currency':'KZT','payments':[{'date':'2026-11-01','amount':'55000'},{'date':'2026-12-01','amount':'55000'}],'extra_costs':'0','schedule_complete':True}

def test_net_received_cash_flow_does_not_add_deducted_costs_twice():
    r=calculate(DATA);assert Decimal(r['cashflow_difference'])==15000
    d=copy.deepcopy(DATA);d['extra_costs']='500';assert Decimal(calculate(d)['cashflow_difference'])==15500


def test_cents_remain_exact_and_duplicate_date_instalments_are_not_silently_removed():
    d=copy.deepcopy(DATA);d.update(amount_received='0.10',extra_costs='0.10');d['payments']=[{'date':'2026-11-01','amount':'0.10'}]*3
    assert Decimal(calculate(d)['cashflow_difference'])==Decimal('0.30')

@pytest.mark.parametrize('change',[
 {'amount_received':'NaN'},{'amount_received':'-1'},{'amount_received':'100.001'},{'amount_received':True},
 {'schedule_complete':False},{'schedule_complete':'true'},{'currency':'RUB'},{'extra_costs':None},
 {'payments':[]},{'payments':[{'date':'2026-01-01','amount':'100000'}]},
 {'payments':[{'date':'2026-12-01','amount':'55000'},{'date':'2026-11-01','amount':'55000'}]},
 {'payments':[{'date':'2026-11-01','amount':'100'}]},
 {'payments':[{'date':'2026-11-01','amount':'55000','currency':'USD'}]},
])
def test_calculator_refuses_ambiguous_or_incomplete_inputs(change):
    d=copy.deepcopy(DATA);d.update(change)
    with pytest.raises(CalculationInputError):calculate(d)


def test_user_table_and_missing_data_get_useful_replies_without_model():
    text=TEMPLATE.split('```text\n')[1].split('\n```')[0]
    answer=calculator_reply(text)
    assert '10 000,00 KZT' in answer and 'Всего последующих выплат' in answer and 'ГЭСВ' in answer
    assert 'Не удалось' not in answer
    help=calculator_reply('!расчёт')
    assert 'График полный' in help and 'персональных' not in help
    assert calculator_reply('Что такое ГЭСВ?') is None


def p(ident,text='Проверенное условие.',**kwargs):
    return dict(id=ident,document_id='law',domain='legal',domains=['legal'],title='Закон о правиле',text=text,source='https://adilet.zan.kz/rus/docs/test',section='Статья 1.',reviewed_on='2026-10-23',**kwargs)


def test_research_preserves_product_and_calls_original_scope_not_reviewer_scope():
    original=p('old',bank='halyk');foreign=p('foreign',bank='freedom');fresh=p('new',bank='halyk')
    class Store:
        def search(self,*args,**kwargs):return [original,foreign,fresh]
    found=research_evidence(Store(),'finance','Какая комиссия Halyk за перевод?','',[original],['Freedom https://evil.example'])
    assert original in found and fresh in found and foreign not in found and len(found)<=8


def test_research_never_uses_future_source():
    old=p('old');future=p('future',valid_from='2027-01-01')
    class Store:
        def search(self,*args,**kwargs):return [old,future]
    assert research_evidence(Store(),'legal','Вопрос','',[old])==[old]


def test_explicit_bounds_and_tariff_due_do_not_certify_current_law():
    assert not applicable(p('x',valid_from='2027-01-01'),date(2026,10,23))
    assert not applicable(p('x',valid_until='2026-10-22'),date(2026,10,23))
    assert not applicable(p('x',valid_from='invalid'),date(2026,10,23))
    assert applicable(p('x'),date(2026,10,23))
    assert tariff_review_due([p('x',bank='halyk')],date(2026,11,1))==['law']


def test_facts_do_not_turn_no_written_contract_into_civil_contract():
    assert case_facts('Начальник не платит, трудового договора нет')['user_reported_facts']['contract_reported']=='no_written_contract'
    assert case_facts('Оплата за работу по договору ГПХ')['user_reported_facts']['contract_reported']=='civil'
    assert case_facts('Начальник не платит')['clarifications']


def test_specific_fact_followup_preserves_user_topic():
    from conversation import ConversationMemory
    memory=ConversationMemory();memory.resolve('r','u','legal','Начальник не отдаёт деньги за работу')
    current,previous=memory.resolve('r','u','legal','У меня договор ГПХ')
    assert previous and 'Начальник' in current
    assert case_facts('У меня договор ГПХ',previous)['user_reported_facts']['contract_reported']=='civil'
    assert memory.resolve('r','other','legal','У меня договор ГПХ')[1]==''






def test_explicit_same_act_reference_keeps_exclusions_and_access_scope():
    old=p('old','Согласно статье 2 настоящего Закона действует исключение.')
    allowed={**p('allowed'),'section':'Статья 2. Исключения'}
    forbidden={**p('forbidden'),'section':'Статья 2. Исключения','excluded_sections':['2']}
    private={**p('private'),'section':'Статья 2. Исключения','domains':['private']}
    class Store:
        passages=[old,allowed,forbidden,private]
        def search(self,*args,**kwargs):return [old]
    assert research_evidence(Store(),'legal','Вопрос','',[old])==[old,allowed]
    changed={**old,'text':'Согласно статье 2 другого кодекса.'}
    assert research_evidence(Store(),'legal','Вопрос','',[changed])==[changed]


def test_new_user_report_overrides_historical_contract_type():
    facts=case_facts('У меня есть трудовой договор','Работал по договору ГПХ')
    assert facts['user_reported_facts']['contract_reported']=='labour'
    facts=case_facts('У меня договор ГПХ\nПредыдущий вопрос: Есть трудовой договор')
    assert facts['user_reported_facts']['contract_reported']=='civil'


def test_generation_failures_are_not_labelled_unexecuted_cases():
    from evaluation_core import summarize
    stats=summarize([{'answer':None,'error':'TimeoutError'},{'answer':None,'error':None}])
    assert stats['not_executed']==1 and stats['generation_failed']==1


def test_current_explicit_contract_overrides_old_absent_contract():
    facts=case_facts('Теперь есть трудовой договор','Начальник не платит, трудового договора нет')
    assert facts['user_reported_facts']['contract_reported']=='labour'


def test_colloquial_consent_and_residence_have_condition_sources():
    from retrieval_intents import query_hints,consent_withdrawal_question,child_residence_question
    assert consent_withdrawal_question('Можно ли забрать согласие на обработку персональных данных?')
    assert not consent_withdrawal_question('Согласие супруга на продажу квартиры')
    assert child_residence_question('Как учитывают мнение ребёнка о проживании?')
    assert not child_residence_question('Какие алименты на ребёнка?')
    assert set(query_hints('Как учитывают мнение ребёнка о проживании?')[2]['family'])=={'Статья 62.','Статья 73.'}
    assert query_hints('Можно ли забрать согласие на обработку данных?')[2]['personal-law']==['Статья 8.']


def test_current_warranty_fact_overrides_history():
    facts=case_facts('Нашёл документы, гарантия есть','Телефон сломался, гарантии нет')
    assert facts['user_reported_facts']['warranty_reported']=='present'


def test_contract_comparison_is_not_reported_civil_relationship():
    from evidence_scope import scope_evidence
    q='Чем трудовой договор отличается от договора ГПХ?'
    assert case_facts(q)['user_reported_facts']['contract_reported']=='unknown'
    sources=[dict(p('l'),document_id='labor'),dict(p('c'),document_id='civil-special')]
    assert len(scope_evidence('legal',q,sources))==2


def test_finance_intents_do_not_confuse_deposit_with_rental_or_auto_repair():
    from retrieval_intents import deposit_guarantee_question,auto_liability_definition,collector_contact_question
    assert deposit_guarantee_question('Если банк лишится лицензии, все мои депозиты вернут полностью?')
    assert not deposit_guarantee_question('Арендодатель не возвращает депозит за квартиру')
    assert auto_liability_definition('Обязательная автостраховка покрывает машину или ответственность?')
    assert not auto_liability_definition('Как получить выплату после ДТП по ОГПО?')
    assert collector_contact_question('Что делать с угрозами от взыскателей долга?')
    assert not collector_contact_question('Судебный исполнитель арестовал счёт')


def test_quality_packet_preserves_breach_exception_and_civil_context():
    from claim_validation import make_quality_packet
    from agents import messages,review_messages
    from review_packet import MARKER
    def packet(q,sources):
        return make_quality_packet('finance',messages('finance',q,sources),review_messages('finance',q,MARKER,sources),{'question':q,'evidence':sources})['quality']
    quality=packet('ГЭСВ и номинальная ставка — какая разница?',[p('rule','Не включаются платежи из-за нарушения условий, включая неустойку и иные виды штрафных санкций.')])
    assert 'apr-excluded-breach-costs' in {c['id'] for c in quality['constraints']}
    quality=packet('У меня договор ГПХ, заказчик не платит',[p('rule','Цена и оплата работы устанавливаются договором.')])
    assert 'reported-civil-contract' in {c['id'] for c in quality['constraints']}


def test_civil_payment_letter_is_actual_template_without_labour_penalty():
    from local_reply import prepare_local_reply
    reply=prepare_local_reply('legal','По договору оказания услуг заказчик не платит. Как составить требование?',None)
    assert reply.mechanism=='organizational_civil_draft'
    assert 'Кому:' in reply.answer and 'Прошу' in reply.answer and '[сумма и валюта]' in reply.answer
    assert 'заработной плате' not in reply.answer and 'в течение' not in reply.answer


def test_unstructured_exact_overpayment_request_gets_dates_and_amounts_template():
    from local_reply import prepare_local_reply
    reply=prepare_local_reply('finance','Сколько переплачу по займу 1000000 тенге на 36 месяцев под 20%?',None)
    assert reply.mechanism=='clarification'
    assert 'график' in reply.answer and 'даты' in reply.answer and '!расчёт' in reply.answer
    assert '600 000' not in reply.answer
