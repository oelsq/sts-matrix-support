"""A SIM-card mention must not turn phone loss into reported bank theft."""
import pytest
import urgent_help

@pytest.mark.parametrize('question',[
 'Учебная ситуация: украли телефон, на нём SIM-карта и банковские приложения. Какие действия сделать в первую очередь, чтобы защитить деньги?',
 'Украли мой телефон и сим-карту. Как защитить деньги в банковских приложениях?',
 'Потерял мобильный телефон с SIM-картой, но списаний не было. Что делать?',
])
def test_phone_loss_keeps_full_question_for_model(question):
 class Knowledge:
  def search(self,*args):raise AssertionError('Do not replace a phone-loss question with a bank-fraud excerpt')
 assert urgent_help.phone_loss_without_reported_bank_fraud(question)
 assert urgent_help.urgent_finance_answer(question,Knowledge()) is None

@pytest.mark.parametrize('question',[
 'Украли телефон, потом списали деньги с банковской карты.',
 'Украли телефон, мошенники требуют перевести деньги на безопасный счёт.',
 'Украли телефон, я уже сообщил SMS-код неизвестному человеку.',
 'С карты списали деньги без моего согласия.',
])
def test_explicit_financial_fraud_still_gets_immediate_guidance(question,monkeypatch):
 class Knowledge:
  def search(self,*args):return [{'document_id':'nb-faq','id':'nb-faq:test','text':'Рекомендуем осуществить блокировку скомпрометированных банковских карт.'}]
 monkeypatch.setattr(urgent_help,'render',lambda domain,answer,*args:answer)
 assert not urgent_help.phone_loss_without_reported_bank_fraud(question)
 answer=urgent_help.urgent_finance_answer(question,Knowledge())
 assert answer is not None and 'заблокировать' in answer
