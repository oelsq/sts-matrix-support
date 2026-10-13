"""Real E2E regressions: generic documents need context; tariffs need the right bank."""
import pytest
from response_guidance import request_clarification,practical_fallback

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q',['А как написать требование?','Как составить заявление?','Напиши мне претензию'])
def test_new_user_gets_subject_question_instead_of_guessed_law_or_refusal(domain,q):
    answer=request_clarification(domain,q)
    assert answer and 'Кому адресовано' in answer and 'персональных данных' in answer
    assert 'нет норм' not in answer and '[S' not in answer
    assert request_clarification(domain,q,'Начальник не отдает деньги за работу') is None
    assert request_clarification(domain,q+'\nПредыдущий вопрос: Начальник не платит зарплату') is None

@pytest.mark.parametrize('q',['Напиши требование работодателю о выплате зарплаты','Как написать претензию продавцу?','Как написать заявление в банк?'])
def test_named_document_subject_is_not_blocked(q):
    assert request_clarification('legal',q) is None


def test_bank_clarification_is_short_and_does_not_override_context_or_general_rights():
    answer=request_clarification('finance','Какая комиссия за оплату коммунальных услуг?')
    assert answer and 'Уточните банк' in answer and 'нет' not in answer
    assert len(answer.split())<55
    assert request_clarification('finance','Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?') is None
    assert request_clarification('finance','А какая комиссия?\nПредыдущий вопрос: Перевод с карты Halyk') is None
    assert request_clarification('finance','Банк незаконно взыскал комиссию. Что делать?') is None


def test_missing_named_product_tariff_keeps_supplied_subject_and_does_not_invent_price():
    q='Какая комиссия за перевод с Kaspi Gold на зарубежную карту?'
    answer=practical_fallback('finance',q)
    assert q in answer and 'соответствующую строку тарифа' in answer
    assert 'Уточните название продукта' not in answer and '0 тенге' not in answer


def test_bank_only_question_still_asks_for_missing_product():
    answer=practical_fallback('finance','Какая комиссия Kaspi?')
    assert 'название продукта' in answer and 'Какой банк' not in answer
