import pytest
from pathlib import Path
from offline_knowledge import KnowledgeStore
from retrieval_intents import image_recording_question, biometric_image_question, image_recording_sections, ordinary_image_scope
from evidence_scope import scope_evidence
from answer_focus import practical_response_focus
from answer_usefulness import usefulness_issues

QUESTIONS = [
    'Можно ли снимать людей в общественных местах?',
    'какие права есть у людей в общественных местах? можно ли снимать фото видео',
    'Разрешено ли фотографировать прохожих на улице?',
    'Могу я записывать видео в парке, если в кадре люди?',
    'Можно снимать полицейского на улице?',
    'Нужно ли разрешение, чтобы выложить чужое фото в TikTok?',
    'Охранник запрещает снимать ценники в магазине',
    'Мои фотографии опубликовали без согласия',
    'Снимаю людей на телефон без распознавания лиц',
]

@pytest.fixture(scope='module')
def knowledge():
    return KnowledgeStore(Path(__file__).resolve().parents[2]/'offline_knowledge')

@pytest.mark.parametrize('q',QUESTIONS)
def test_image_questions_retrieve_image_right_instead_of_public_nuisance(knowledge,q):
    evidence=knowledge.search('legal',q,limit=8)
    assert any(p['document_id']=='civil-general' and p.get('section','').startswith('Статья 145.') for p in evidence)
    scoped=scope_evidence('legal',q,evidence)
    assert scoped and all(p['document_id']!='administrative-offences' for p in scoped)

@pytest.mark.parametrize('q',['Система видеонаблюдения с распознаванием лиц в торговом центре', 'Как обрабатывать биометрические данные с камеры?'])
def test_real_biometric_question_keeps_its_own_source_family(q):
    assert biometric_image_question(q)
    assert not ordinary_image_scope(q)
    assert 'personal-law' in image_recording_sections(q)

@pytest.mark.parametrize('q',['Можно ли снять наличные со счёта?', 'Как снять квартиру?', 'Как приложить фото документа к заявлению по кредиту?', 'Почему не играет видео на телефоне?', 'Можно ли использовать изображение товарного знака в рекламе?'])
def test_unrelated_senses_of_take_and_video_do_not_trigger_photo_rights(q):
    assert not image_recording_question(q)

@pytest.mark.parametrize('q',['Можно ли снимать судебное заседание?', 'Можно ли фотографировать в больнице?', 'Кому принадлежат авторские права на фото?'])
def test_special_cases_are_not_restricted_to_generic_image_sources(q):
    assert not ordinary_image_scope(q)


def test_shop_filming_includes_consumer_rule(knowledge):
    q='Охранник запрещает снимать ценники в магазине'
    evidence=knowledge.search('legal',q,limit=8)
    assert any(p['document_id']=='consumer-law' and 'ограничивать права потребителей в отношении применения фото-' in p['text'] for p in evidence)


def test_plain_camera_does_not_receive_biometric_hardware_task():
    text=practical_response_focus(QUESTIONS[0])
    assert 'Раздели фиксацию изображения' in text
    assert 'не обсуждай без такого вопроса' in text


def test_advice_only_does_not_count_as_an_answer_to_permission_question():
    text='Сохраните характеристики камеры.\n\nУточните у разработчика параметры.\n\nВы будете публиковать видео?\n\n**Ответ подтверждён частично**\nПравовое основание отдельного требования ещё нужно проверить.'
    assert 'direct_explanation_missing' in usefulness_issues(QUESTIONS[0],text)

@pytest.mark.parametrize('answer',[
    'Да, можно.',
    'Съёмку и публикацию изображения нужно рассматривать отдельно. Использование изображения регулируется отдельными условиями.',
    'Без сведений о месте и целях записи нельзя определить применимые ограничения. Уточните, где производится запись.',
    'Досрочное погашение возможно при соблюдении применимых условий договора и закона.',
])
def test_qualified_explanation_or_concrete_uncertainty_is_not_advice_only(answer):
    assert 'direct_explanation_missing' not in usefulness_issues('Можно ли это делать?',answer)


def test_personal_use_scope_is_available_but_not_replaced_by_biometric_definitions(knowledge):
    q='Можно ли снимать людей в общественных местах?'
    evidence=scope_evidence('legal',q,knowledge.search('legal',q,limit=8))
    assert any(p['document_id']=='personal-law' and p.get('section','').startswith('Статья 3.') for p in evidence)
    assert any(p['document_id']=='constitution' and p.get('section','')=='Статья 23' for p in evidence)
    assert not any(p['document_id']=='personal-law' and p.get('section','').startswith('Статья 1.') for p in evidence)
