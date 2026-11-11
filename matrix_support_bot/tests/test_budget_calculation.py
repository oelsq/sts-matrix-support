"""Actual budget arithmetic, including overlap, units and missing inputs."""
import pytest
from arithmetic_reply import arithmetic_reply

BASE='Составь простой бюджет в тенге: доход 350000, обязательные расходы 180000, платёж по кредиту 50000, резерв 50000. Сколько остаётся свободных денег? Не придумывай доходность инвестиций.'

def test_complete_budget_is_exact_and_explains_separate_categories():
 answer=arithmetic_reply(BASE)
 assert '70 000 тенге' in answer
 assert '350 000 тенге − 180 000 тенге − 50 000 тенге − 50 000 тенге' in answer
 assert 'отдельные суммы' in answer

@pytest.mark.parametrize('income,expenses,loan,reserve,result',[
 ('100000','50000','20000','10000','20 000'),
 ('100000','90000','20000','10000','-20 000'),
 ('0,35 млн','180 тыс','50 тыс','50 тыс','70 000'),
 ('1000,50','500,20','100,10','100,10','300,1'),
])
def test_budget_uses_all_four_inputs_with_decimal_precision(income,expenses,loan,reserve,result):
 q=f'Бюджет: доход {income} тенге, расходы {expenses} тенге, кредит {loan} тенге, резерв {reserve} тенге.'
 assert result in arithmetic_reply(q)


def test_overlapping_expenses_need_clarification():
 q=BASE+' Кредит уже учтен в обязательных расходах.'
 answer=arithmetic_reply(q)
 assert 'Уточнение бюджета' in answer and 'дважды' in answer
 assert '70 000' not in answer


def test_mixed_currencies_are_not_silently_added():
 q='Бюджет: доход 350000 тенге, расходы 180000 тенге, кредит 50000 USD, резерв 50000 тенге.'
 assert 'одной валюте' in arithmetic_reply(q)

@pytest.mark.parametrize('q',[
 BASE.replace('доход 350000','доход -350000'),
 BASE+' Еще питание 10000 тенге.',
 BASE.replace(', резерв 50000',''),
 BASE.replace('доход 350000','доход до налогов 350000'),
 'Напиши исковое заявление о бюджете: доход 350000, расходы 180000, кредит 50000, резерв 50000.',
])
def test_incomplete_normative_or_extra_inputs_do_not_get_a_false_budget(q):
 assert arithmetic_reply(q) is None
