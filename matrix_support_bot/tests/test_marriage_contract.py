from pathlib import Path
import pytest
from checked_answers import checked_legal_answer
from local_reply import prepare_local_reply
from offline_knowledge import KnowledgeStore

@pytest.fixture(scope='module')
def knowledge():return KnowledgeStore(Path(__file__).resolve().parents[2]/'offline_knowledge')

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question',['Что регулирует брачный договор?','Что такое брачный договор?'])
def test_definition_keeps_child_property_separate_from_spousal_obligations_toward_children(knowledge,domain,question):
    prepared=prepare_local_reply(domain,question,knowledge)
    assert prepared.mechanism=='source_bound_legal'
    assert 'допускаются условия об имущественном положении детей' in prepared.answer
    assert 'права и обязанности в отношении детей' in prepared.answer
    assert 'Не удалось' not in prepared.answer
    assert 'Цитата из документа' in prepared.answer

@pytest.mark.parametrize('changed_phrase',['имущественное положение детей','нетрудоспособного нуждающегося супруга','имущественные права и обязанности супругов'])
def test_definition_is_not_returned_when_a_required_qualification_is_missing(knowledge,changed_phrase):
    question='Что регулирует брачный договор?';evidence=knowledge.search('legal',question,8)
    evidence=[dict(p,text=p['text'].replace(changed_phrase,'Изменённая норма')) for p in evidence]
    assert checked_legal_answer(question,evidence) is None


def test_personal_dispute_or_document_draft_does_not_get_the_definition(knowledge):
    question='Составь брачный договор для моей ситуации'
    assert checked_legal_answer(question,knowledge.search('legal',question,8)) is None
