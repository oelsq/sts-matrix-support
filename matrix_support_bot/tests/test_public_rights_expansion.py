"""New public law must be retrievable, cited, bounded, and usable in both rooms."""
import hashlib
import json
import re
from pathlib import Path
import pytest
from answer_usefulness import department_note, usefulness_issues
from checked_public_rights import checked_public_rights_answer
from conversation import ConversationMemory
from evidence_scope import scope_evidence
from local_reply import prepare_local_reply
from message_format import matrix_content
from offline_knowledge import KnowledgeStore

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT/'config/expanded-helpfulness-evaluation.json').read_text())['cases']


@pytest.fixture(scope='module')
def store():
    return KnowledgeStore(ROOT/'offline_knowledge')


@pytest.mark.parametrize('case', CASES, ids=lambda c:c['id'])
def test_actual_local_reply_solves_requested_task_with_real_source_clauses(store,case):
    memory=ConversationMemory()
    for previous in case['previous_questions']:
        memory.resolve('room','user',case['domain'],previous)
    search,previous=memory.resolve('room','user',case['domain'],case['question'])
    reply=prepare_local_reply(case['domain'],case['question'],store,search_question=search,previous_question=previous)
    assert reply.mechanism=='source_bound_public_rights'
    assert reply.answer and 'Не удалось подготовить' not in reply.answer
    assert not usefulness_issues(case['question'],reply.answer)
    assert department_note(case['question'],reply.answer)==''
    assert matrix_content(reply.answer).get('formatted_body')
    for pattern in case['required_patterns']:
        assert re.search(pattern,reply.answer,re.I)
    assert 'обратитесь в департамент' not in reply.answer.casefold()


@pytest.mark.parametrize('case',[c for c in CASES if c['domain']=='legal'],ids=lambda c:c['id'])
def test_no_sources_cannot_activate_a_factual_public_rights_explanation(case):
    assert checked_public_rights_answer(case['domain'],case['question'],[]) is None


@pytest.mark.parametrize('question,document,clause',[
 ('В кредитной истории ошибка: кредит закрыт. Как исправить?', 'credit-history','заявлять несогласие с информацией'),
 ('Начальник требует выйти из профсоюза. Что делать?', 'trade-unions','Запрещается воздействие на лиц угрозой'),
 ('Покупаю квартиру в новостройке. Какие документы запросить для долевого участия?', 'shared-construction','о наличии разрешения местного исполнительного органа'),
 ('После ДТП страховщик удержал расходы на оценку ущерба. Это допустимо?', 'auto-insurance','Страховщик не вправе удерживать со страховой выплаты'),
])
def test_a_changed_or_missing_defining_clause_disables_the_bounded_reply(store,question,document,clause):
    evidence=scope_evidence('legal',question,store.search('legal',question,8))
    assert checked_public_rights_answer('legal',question,evidence)
    changed=[{**p,'text':p['text'].replace(clause,'[изменённая норма]')} for p in evidence]
    assert checked_public_rights_answer('legal',question,changed) is None


def test_sources_preserve_original_integrity_status_and_future_exclusions(store):
    manifest=json.loads((ROOT/'offline_knowledge/manifest.json').read_text())['documents']
    catalog=json.loads((ROOT/'offline_knowledge/extended-catalog-20261021.json').read_text())['documents']
    assert len(catalog)==24
    for spec in catalog:
        item=next(x for x in manifest if x['id']==spec['id'])
        raw=(ROOT/'offline_knowledge'/item['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==item['sha256']
        assert item['reviewed_on']=='2026-10-21'
        assert item['source']=='https://old.adilet.zan.kz/rus/docs/'+spec['code']
        assert item['status'] in {'Обновленный','Обновлённый','Исправление ошибки','Новый'}
        assert item['artifact_kind']=='official_text_snapshot'
        proof=json.loads((ROOT/'offline_knowledge'/item['source_evidence']).read_text())
        assert proof['text_sha256']==item['sha256']
        assert proof['excluded_sections']==item.get('excluded_sections',[])
        # Full source remains available even where unresolved future rules are
        # withheld from retrieved evidence, rather than edited out of history.
        for article in item.get('excluded_sections',[]):
            evidence=store.search('legal',item['title'],10000)
            assert not any(p['document_id']==item['id'] and
                re.match(r'^Статья '+re.escape(article)+r'(?:\.|\s|$)',p['section']) for p in evidence)
    assert next(x for x in manifest if x['id']=='permissions')['status']=='Исправление ошибки'


def test_unrelated_letters_and_actual_arrears_are_not_recast_as_credit_history_errors(store):
    q='В кредитной истории правдиво указаны мои просрочки. Удали их и гарантируй новый кредит.'
    evidence=scope_evidence('legal',q,store.search('legal',q,8))
    assert checked_public_rights_answer('legal',q,evidence) is None
    q='Напиши иск в суд о возмещении ущерба после ДТП'
    evidence=scope_evidence('legal',q,store.search('legal',q,8))
    assert checked_public_rights_answer('legal',q,evidence) is None


@pytest.mark.parametrize('domain',['legal','finance'])
def test_suspicious_loan_status_warning_stays_ahead_of_generic_credit_history(store,domain):
    question='Получил уведомление о статусе чужого займа. Где проверить кредитную историю?'
    evidence=scope_evidence(domain,question,store.search(domain,question,8))
    assert evidence[0]['document_id']=='nb-phishing-loan'
    answer=checked_public_rights_answer(domain,question,evidence)
    assert answer and 'Не переходите по ссылке' in answer
    assert 'ошибка' not in answer.split('Локальные материалы')[0]


def test_a_shared_procedure_and_refusal_passage_has_only_one_source_card(store):
    question='В кредитной истории ошибка: кредит закрыт. Как исправить?'
    evidence=scope_evidence('legal',question,store.search('legal',question,8))
    answer=checked_public_rights_answer('legal',question,evidence)
    cards=re.findall(r'^• \[([^\]]+)\]',answer,re.M)
    assert cards and len(cards)==len(set(cards))


@pytest.mark.parametrize('question',[
    'Пришло сообщение: статус моего займа обновлен',
    'СМС со ссылкой про статус моего заёма',
    'Уведомление об обновлении статуса заема',
])
def test_notification_intent_recognizes_russian_loan_forms(question):
    from retrieval_intents import loan_status_notification
    assert loan_status_notification(question)


def test_renting_in_a_new_building_is_not_replaced_with_a_dolshchik_checklist(store):
    question='Хочу арендовать квартиру в новостройке. Какие документы запросить у хозяина?'
    evidence=scope_evidence('legal',question,store.search('legal',question,8))
    assert checked_public_rights_answer('legal',question,evidence) is None


@pytest.mark.parametrize('question',[
    'В кредитной истории ошибка. За сколько дней должны рассмотреть заявление?',
    'Напиши иск в суд об исправлении ошибки в кредитной истории.',
    'Уволили из-за профсоюза. Рассчитай компенсацию.',
])
def test_a_general_checklist_does_not_replace_a_deadline_calculation_or_court_document(store,question):
    evidence=scope_evidence('legal',question,store.search('legal',question,8))
    assert checked_public_rights_answer('legal',question,evidence) is None
