from pathlib import Path
import pytest
from offline_knowledge import KnowledgeStore
from local_reply import prepare_local_reply
from checked_finance_terms import checked_gesv_answer
from official_sources import parse_official_page,SourceError

@pytest.fixture(scope='module')
def knowledge():return KnowledgeStore(Path(__file__).resolve().parents[2]/'offline_knowledge')

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q',['Что такое ГЭСВ и чем отличается от номинальной ставки?','Включаются ли штрафы и страховка в ГЭСВ?'])
def test_actual_rules_include_costs_and_preserve_exclusions(knowledge,domain,q):
    r=prepare_local_reply(domain,q,knowledge)
    assert r.mechanism=='source_bound_gesv'
    assert 'штрафные санкции, исключены' in r.answer
    assert 'Не удалось' not in r.answer and 'не сводится' in r.answer
    assert '2026-04-03' in r.answer
    assert checked_gesv_answer(domain,q,[]) is None
    changed=[{**p,'text':p['text'].replace('включая неустойку и иные виды штрафных санкций','иная норма')} for p in r.evidence]
    assert checked_gesv_answer(domain,q,changed) is None


def test_mfo_or_calculation_does_not_get_bank_definition_as_its_answer(knowledge):
    q='Рассчитай ГЭСВ микрокредита МФО'
    assert checked_gesv_answer('finance',q,knowledge.search('finance',q,8)) is None


def test_bank_fee_source_is_an_explicit_dated_excerpt_not_repealed_list(knowledge):
    sources=[p for p in knowledge.passages if p['document_id']=='bank-fees-2026']
    assert sources and all(p['version']=='2026-07-24' for p in sources)
    assert any('договоров добровольного страхования' in p['text'] for p in sources)
    assert all(p['source'].endswith('V2600038748') for p in sources)


def test_importer_rejects_repealed_regulations_before_activation():
    html='<h1>Правила</h1>Утративший силу Постановление Правления<article>'+('1. Действие нормы.\n'*10)+'</article>'
    with pytest.raises(SourceError,match='Repealed'):parse_official_page(html,document_kind='regulatory_rules')
