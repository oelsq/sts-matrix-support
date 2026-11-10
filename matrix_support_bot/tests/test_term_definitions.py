"""Definition completeness, provenance and boundaries; no expert-certification claim."""
from pathlib import Path
from types import SimpleNamespace
import copy,json,re
import pytest

from term_definitions import definition_term, definition_parts, checked_term_definition, norm
from local_reply import prepare_local_reply, prepare_model_evidence
from offline_knowledge import KnowledgeStore
from case_facts import case_facts
from agents import messages, review_messages
from claim_validation import make_quality_packet, validate_audit, question_type
from review_packet import MARKER
from answer_usefulness import answer_body

ROOT=Path(__file__).resolve().parents[2]
DATA=json.loads((ROOT/'config/definition-cases-20261101.json').read_text())

@pytest.fixture(scope='module')
def corpus():
    return KnowledgeStore(ROOT/'offline_knowledge')


@pytest.mark.parametrize('case',DATA['cases'],ids=lambda c:c['id'])
def test_reformulations_receive_a_short_source_bound_definition(corpus,case):
    assert definition_term(case['question'])==case['term']
    result=prepare_local_reply(case['domain'],case['question'],corpus)
    assert result.mechanism=='source_bound_definition'
    body=answer_body(result.answer)
    for p in case['required_patterns']:assert re.search(p,body,re.I)
    for p in case['forbidden_patterns']:assert not re.search(p,body,re.I)
    assert len(body.split())<=case['max_body_words']
    from message_format import matrix_content
    displayed=matrix_content(result.answer)
    assert 'Источники' in displayed['body'] and displayed['format']=='org.matrix.custom.html'
    assert 'Не удалось' not in result.answer
    facts=case_facts(case['question'])
    assert facts['kind']=='reference' and facts['user_reported_facts']=={}
    assert question_type(case['question'])=='definition'


@pytest.mark.parametrize('question',DATA['negative_definition_intents'])
def test_a_definition_does_not_replace_another_task(question):
    assert definition_term(question) is None
    assert checked_term_definition('legal',question,[]) is None


@pytest.mark.parametrize('term',['gph','gesv'])
@pytest.mark.parametrize('domain',['legal','finance'])
def test_model_gets_exact_anchors_without_fabricated_sources(corpus,term,domain):
    q='Что такое ГПХ?' if term=='gph' else 'Что такое ГЭСВ?'
    evidence=prepare_model_evidence(domain,q,corpus)
    before=copy.deepcopy(evidence)
    packet=make_quality_packet(domain,messages(domain,q,evidence),review_messages(domain,q,MARKER,evidence),
                               {'question':q,'previous_question':'','evidence':evidence})
    assert evidence==before
    assert packet['quality']['question_type']=='definition'
    assert 'reported-civil-contract' not in [c['id'] for c in packet['quality']['constraints']]
    assert 'полную расшифровку' in packet['draft_system']
    assert 'quote_id' in packet['draft_user']
    assert len(packet['quality']['sources'])==len(evidence)
    parts=definition_parts(q,evidence)
    assert len(parts)==(2 if term=='gph' else 4)
    if term=='gesv':
        assert any(p.get('document_id')=='gesv-rules-2026' and p.get('section','').startswith('Пункт 1.')
                   and 'Для целей Правил' in p['text'] for p in evidence)


def test_pdf_line_breaks_and_negative_heading_keep_all_required_quote_ids(corpus):
    from term_definitions import definition_focus,CONTRACT
    for q in ['Что такое ГПХ?','Что такое ГЭСВ?']:
        e=prepare_model_evidence('legal',q,corpus)
        p=make_quality_packet('legal',messages('legal',q,e),review_messages('legal',q,MARKER,e),
                              {'question':q,'previous_question':'','evidence':e})
        plan,quotes=definition_focus(q,e,p['quality']['sources'])
        target=next(x for x in plan if x['part']==('понятие договора' if 'ГПХ' in q else 'исключение для нарушения договора'))
        assert len(target['quote_ids'])>=2
        actual=' '.join(x['quote'] for x in quotes if x['source_id']==target['source_id'])
        if 'ГПХ' in q:assert norm(CONTRACT) in norm(actual)
        else:assert 'не включаются' in actual and 'неустойку' in actual
        # A genuine definition anchor never certifies an unrelated numeric claim.
        audit={'verdict':'approve','checks':[{'unit_id':'C1','status':'supported','proofs':[{'source_id':quotes[0]['source_id'],'quote':quotes[0]['quote']}]}]}
        assert validate_audit('Банк обязан вернуть 99 тенге. [S1]',audit,p['quality']['sources'])


@pytest.mark.parametrize('term',['gph','gesv'])
def test_missing_or_changed_essential_source_does_not_get_a_prepared_definition(corpus,term):
    q='Что такое ГПХ?' if term=='gph' else 'Что такое ГЭСВ?'
    e=prepare_model_evidence('legal',q,corpus)
    assert checked_term_definition('legal',q,e)
    for doc in (['gph-term-guide','civil-general'] if term=='gph' else ['gesv-term-guide','gesv-rules-2026']):
        assert checked_term_definition('legal',q,[p for p in e if p['document_id']!=doc]) is None
    changed=[{**p,'text':p['text'].replace('вознаграждения','платежа').replace('признается','не признается')} for p in e]
    assert checked_term_definition('legal',q,changed) is None


def test_penalty_exception_is_preserved_and_deposit_does_not_inherit_loan_costs(corpus):
    q='Объясни ГЭСВ простыми словами'
    e=prepare_model_evidence('finance',q,corpus)
    answer=checked_term_definition('finance',q,e)
    assert 'штрафы за нарушение' in answer and 'не включаются' in answer
    deposit='Что такое ГЭСВ по депозиту?'
    e=prepare_model_evidence('finance',deposit,corpus)
    answer=checked_term_definition('finance',deposit,e)
    assert answer and 'основному долгу' not in answer and 'штрафы' not in answer


def test_dense_search_cannot_displace_the_essential_lexical_anchors(corpus):
    old=corpus.semantic
    candidates=[p for p in corpus.passages if p['document_id']=='civil-special']
    corpus.semantic=SimpleNamespace(search=lambda q,eligible,limit: [p for p in candidates if p['id'] in eligible][:limit])
    try:
        for q in ['ГПХ — это что?','Что значит ГЭСВ?']:
            e=prepare_model_evidence('finance',q,corpus)
            assert definition_parts(q,e)
            assert checked_term_definition('finance',q,e)
    finally:corpus.semantic=old


def test_real_civil_payment_case_is_still_a_reported_contract():
    facts=case_facts('Заказчик по ГПХ не оплатил работу')
    assert facts['kind']=='work_payment' and facts['user_reported_facts']['contract_reported']=='civil'


def test_new_alias_source_is_only_a_short_integrity_checked_label(corpus):
    import hashlib
    m=json.loads((ROOT/'offline_knowledge/manifest.json').read_text())
    entry=next(x for x in m['documents'] if x['id']=='gesv-term-guide')
    text=(ROOT/'offline_knowledge'/entry['file']).read_bytes()
    assert hashlib.sha256(text).hexdigest()==entry['sha256']
    assert entry['document_kind']=='official_guidance' and len(text.decode().split())<=25
    assert not re.search(r'\d|штраф|обязан|предельн',text.decode(),re.I)



def test_deposit_definition_does_not_acquire_a_loan_penalty_constraint(corpus):
    q='Что такое ГЭСВ по депозиту?'
    e=prepare_model_evidence('finance',q,corpus)
    p=make_quality_packet('finance',messages('finance',q,e),review_messages('finance',q,MARKER,e),
                          {'question':q,'previous_question':'','evidence':e})
    assert 'apr-excluded-breach-costs' not in [c['id'] for c in p['quality']['constraints']]



@pytest.mark.parametrize('question',[
    'Что такое ГЭСВ и сколько будет 10% от миллиона?',
    'Что такое ГПХ и можно ли по нему уволить человека?',
    'Что означает ГЭСВ и когда банк должен её пересчитать?',
    'Что такое договор ГПХ и сколько мне заплатят?',
])
def test_a_second_task_is_never_hidden_by_a_short_definition(question):
    assert definition_term(question) is None
