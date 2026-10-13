"""New public guidance is shared, dated, and never mislabelled as legislation."""
from pathlib import Path
import pytest
from offline_knowledge import KnowledgeStore
from evidence_scope import scope_evidence

@pytest.fixture(scope='module')
def store():return KnowledgeStore(Path(__file__).resolve().parents[2]/'offline_knowledge')

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question,doc',[
 ('Можно заработать на цифровом тенге с поддержкой Нацбанка?','nb-digital-tenge-fraud'),
 ('СМС: статус вашего займа был обновлен. Открыть ссылку?','nb-phishing-loan'),
 ('Как включить Стоп-кредит?','stop-credit-guide'),
 ('Работодатель не внес трудовой договор в ЕСУТД','labor-esutd-guide')])
def test_new_guidance_is_found_and_preserves_source_kind(store,domain,question,doc):
    found=scope_evidence(domain,question,store.search(domain,question,8))
    source=next(p for p in found[:5] if p['document_id']==doc)
    assert source['document_kind']=='official_guidance'
    assert source['reviewed_on']=='2026-10-13' and source['applicability_note']
    assert source['source'].startswith(('https://nationalbank.kz/','https://www.gov.kz/'))

def test_official_pdf_dates_and_conditions_are_preserved(store):
    packets=[p for p in store.passages if p['document_id']=='nb-digital-tenge-fraud']
    assert packets and all(p['version']=='2026-03-30' for p in packets)
    assert any('не является' in ' '.join(p['text'].split()) and 'инвестиционным' in p['text'] for p in packets)
    assert any('ломбарды' in p['text'] for p in store.passages if p['document_id']=='stop-credit-guide')
    assert any('не означает' in p['text'] and 'недействительным' in p['text'] for p in store.passages if p['document_id']=='labor-esutd-guide')


@pytest.mark.parametrize('domain',['legal','finance'])
def test_informal_work_keeps_employer_fault_condition_and_separate_point_four(store,domain):
    from checked_answers import checked_legal_answer
    q='Работал без оформления трудового договора. Есть ли у меня права?'
    evidence=scope_evidence(domain,q,store.search(domain,q,8))
    answer=checked_legal_answer(q,evidence,domain=domain)
    assert answer and 'по вине работодателя' in answer
    assert 'со дня, когда работник приступил' in answer
    assert 'компенсацию за неиспользованный отпуск' not in answer
    assert 'иск в суд' not in answer
    assert checked_legal_answer(q,[],domain=domain) is None
    altered=[{**p,'text':p['text'].replace('по вине работодателя','по другой причине')} for p in evidence]
    assert checked_legal_answer(q,altered,domain=domain) is None
    assert checked_legal_answer('Без оформления договора: рассчитай компенсацию за отпуск',evidence,domain=domain) is None


def test_safe_account_question_gets_direct_source_bound_warning_without_llm(store):
    from urgent_help import urgent_finance_answer
    answer=urgent_finance_answer('Мне предлагают перевести деньги на безопасный счет. Это нужно делать?',store)
    assert answer and 'Не переводите деньги' in answer and '2026-02-17' in answer
    assert 'Нацбанк: мошенническая блокировка карты' in answer
    assert urgent_finance_answer('Как открыть депозит?',store) is None
