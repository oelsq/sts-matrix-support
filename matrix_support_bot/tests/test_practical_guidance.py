"""Reviewed guidance complements statutes; both specialists can find it."""
import json
from pathlib import Path
import pytest
from evidence_scope import scope_evidence
from offline_knowledge import KnowledgeStore
from checked_answers import checked_legal_answer, checked_work_payment_answer

@pytest.fixture(scope='module')
def corpus():
    return KnowledgeStore(Path(__file__).resolve().parents[2]/'offline_knowledge')

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question,document',[
    ('Нечем платить кредит. Можно снизить платеж?','regulator-restructure'),
    ('Банк отказал в реструктуризации. Куда обращаться?','regulator-refusal'),
    ('Как составить претензию продавцу?','consumer-claim-guide'),
    ('Не платят зарплату. Куда жаловаться?','labor-wage-guide'),
])
def test_helpful_official_guidance_is_retrieved_in_both_rooms(corpus,domain,question,document):
    evidence=scope_evidence(domain,question,corpus.search(domain,question,8))
    assert any(p['document_id']==document for p in evidence)
    guide=next(p for p in evidence if p['document_id']==document)
    assert guide['document_kind']=='official_guidance'
    assert guide['source'].startswith('https://www.gov.kz/')
    assert guide['applicability_note']


def test_wage_complaint_uses_inspection_guidance_instead_of_initial_wage_template(corpus):
    q='Не платят зарплату. Куда жаловаться?'
    evidence=scope_evidence('finance',q,corpus.search('finance',q,8))
    assert checked_work_payment_answer('finance',q,evidence) is None
    assert any(p['document_id']=='labor-wage-guide' for p in evidence)


def test_source_bound_phone_explanation_applies_in_finance_room_without_false_category(corpus):
    q='Купил неисправный телефон. Могу ли попросить возврат денег?'
    evidence=corpus.search('finance',q,8)
    answer=checked_legal_answer(q,evidence,domain='finance')
    assert answer and 'Финансовый агент' in answer
    assert 'телефон не включён в закрытый перечень' in answer
    assert 'только при существенном недостатке' not in answer
    assert checked_legal_answer('Напиши претензию на неисправный телефон',evidence) is None

@pytest.mark.parametrize('domain',['legal','finance'])
def test_restructure_guidance_and_letter_require_actual_source_and_keep_intent(corpus,domain):
    from checked_answers import checked_practical_guidance
    from response_guidance import retrieval_question, practical_fallback
    first='Нечем платить кредит. Можно снизить платеж?'
    evidence=scope_evidence(domain,first,corpus.search(domain,first,8))
    answer=checked_practical_guidance(domain,first,evidence)
    assert answer and 'попросить реструктуризацию' in answer and 'не обещание одобрения' in answer
    q=retrieval_question('А напиши заявление в банк.',first)
    evidence=scope_evidence(domain,q,corpus.search(domain,q,8))
    draft=checked_practical_guidance(domain,q,evidence)
    assert draft and 'Черновик заявления' in draft and '[сумма]' in draft and '[дата]' in draft
    assert '15 календарных' not in draft and '30 календарных' not in draft
    assert checked_practical_guidance(domain,q,[]) is None
    fallback=practical_fallback(domain,q,validation_failed=True)
    assert 'Черновик заявления' in fallback and '[S1]' not in fallback
    for current in ['А рассчитай платёж?', 'А напиши жалобу в суд?', 'А сколько пени?']:
        assert checked_practical_guidance(domain,current+'\nПредыдущий вопрос: '+first,evidence) is None
    fake=[{**p,'text':'Здесь просто упоминается реструктуризация.'} for p in evidence]
    assert checked_practical_guidance(domain,q,fake) is None

@pytest.mark.parametrize('domain',['legal','finance'])
def test_consumer_letter_preserves_phone_definition_and_does_not_invent_defect(corpus,domain):
    from checked_answers import checked_practical_guidance
    q='А напиши претензию продавцу.\nПредыдущий вопрос: Купил неисправный телефон. Могу ли попросить возврат денег?'
    ev=corpus.search(domain,q,8)
    letter=checked_practical_guidance(domain,q,ev)
    assert letter and 'Черновик претензии' in letter and '[описание неисправности]' in letter
    assert 'По моим данным' in letter and '[дата покупки]' in letter
    assert 'обнаружен существенный недостаток' not in letter
    assert '3 рабочих' not in letter and '10 календарных' not in letter
    assert checked_practical_guidance(domain,q,[]) is None
    for current in ['Напиши претензию о ремонте','Напиши жалобу в министерство','Напиши иск в суд']:
        assert checked_practical_guidance(domain,current+'\nПредыдущий вопрос: '+q,ev) is None


def test_explicit_bank_question_does_not_use_mfo_specific_procedure(corpus):
    q='Банк отказал в реструктуризации. Куда обращаться?'
    ev=scope_evidence('finance',q,corpus.search('finance',q,8))
    assert all(p['document_id']!='microfinance' for p in ev)
    from checked_answers import checked_practical_guidance
    answer=checked_practical_guidance('finance',q,ev)
    assert answer and 'банковскому омбудсману' in answer
    assert '30 календарных' not in answer and 'обязан изменить' not in answer


def test_wage_complaint_is_bound_to_inspection_guidance_without_formula_or_false_prerequisite(corpus):
    from checked_answers import checked_practical_guidance
    q='Не платят зарплату. Куда жаловаться?'
    ev=scope_evidence('legal',q,corpus.search('legal',q,8))
    answer=checked_practical_guidance('legal',q,ev)
    assert answer and 'инспекцию труда' in answer and 'e-Өтініш' in answer
    assert '1,25' not in answer and 'сначала обязан' not in answer
    assert checked_practical_guidance('legal',q,[]) is None
    letter='Напиши жалобу в инспекцию труда.\nПредыдущий вопрос: Начальник не платит зарплату'
    assert checked_work_payment_answer('legal',letter,ev) is None
