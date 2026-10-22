import pytest
from evidence_scope import scope_evidence
from response_guidance import is_reference_question

AUTO = {'document_id': 'auto-insurance', 'id': 'coverage', 'section': 'Статья 22. Определение страхового случая',
        'text': 'Статья 22. Определение страхового случая\n1. Страховым случаем признается ответственность за вред потерпевшим.\n2. Порядок оценки повреждений.'}
EXCLUSION = {'document_id': 'auto-insurance', 'id': 'other-rule', 'section': 'Статья 13. Действие договора',
             'text': 'Исключение для отдельных категорий потерпевших, не определение объекта.'}


def test_insurance_definition_omits_damage_procedures_and_preserves_source_identity():
    source = [AUTO, EXCLUSION]
    result = scope_evidence('finance', 'Что страхуется по обязательному автострахованию: машина или ответственность?', source)
    assert len(result) == 1 and result[0]['id'] == 'coverage'
    assert 'ответственность за вред' in result[0]['text']
    assert 'Порядок оценки' not in result[0]['text']
    assert 'Порядок оценки' in AUTO['text']  # Scoping must not mutate the stored original.


def test_real_accident_question_retains_procedure_and_exclusions():
    source = [AUTO, EXCLUSION]
    result = scope_evidence('legal', 'Что покрывает автострахование при ДТП и как получить выплату?', source)
    assert result == source


@pytest.mark.parametrize('question', [
    'Что страхуется по обязательному автострахованию?',
    'Что покрывает полис?',
    'Чем отличается ГЭСВ от номинальной ставки?',
])
def test_short_definitions_do_not_request_a_procedure(question):
    assert is_reference_question(question)


@pytest.mark.parametrize('question', [
    'Что страхуется и что делать после ДТП?',
    'Страховая отказала, как обжаловать?',
])
def test_request_for_action_is_not_shortened_to_a_definition(question):
    assert not is_reference_question(question)
