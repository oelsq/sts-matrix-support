import importlib.util
from pathlib import Path
import pytest
from message_format import matrix_content

ROOT=Path(__file__).resolve().parents[2]
if ROOT == Path('/') and not (ROOT/'scripts/prepare_matrix_review.py').is_file():
    pytest.skip('Repository-level campaign tooling is not shipped in the standalone bot image', allow_module_level=True)
spec=importlib.util.spec_from_file_location('prepare_matrix_review',ROOT/'scripts/prepare_matrix_review.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_delivered_numbered_citations_restore_exact_fragment_ids():
    passage={'id':'family:68:1','document_id':'family','source':'https://example.org/act','text':'Точный текст нормы.'}
    raw=('Ответ с опорой. [family:68:1]\n\nЛокальные материалы для проверки:\n'
         '• [family:68:1] Кодекс, Статья 41 (проверен 2026-10-29; редакция/версия: snapshot; источник: https://example.org/act)\n'
         '  Цитата из документа: «Точный текст нормы.»')
    delivered=matrix_content(raw)['body']
    assert '[1]' in delivered and 'Фрагмент: family:68:1.' in delivered
    answer,evidence=module.delivered_sources(delivered,{passage['id']:passage})
    assert '[family:68:1]' in answer and evidence[0]['id']==passage['id']
    assert evidence[0]['text']==passage['text'] and evidence[0]['document']=='family'


@pytest.mark.parametrize('text',[
    'Ответ [1].\nИсточники\nФрагмент: missing:1.\nЦитата из документа: «текст».',
    'Ответ [1].\nИсточники\nЦитата из документа: «текст без точного ID».',
])
def test_bridge_fails_closed_instead_of_assessing_without_required_evidence(text):
    with pytest.raises(ValueError):module.delivered_sources(text,{})


def test_an_organisational_letter_needs_no_invented_source():
    text='Кому: [адресат]\nПрошу предоставить ответ.'
    answer,evidence=module.delivered_sources(text,{})
    assert answer==text and evidence==[]
