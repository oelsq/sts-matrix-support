"""Small, editable presentation examples; never a source of facts or law."""
from functools import lru_cache
import json
from pathlib import Path
import re


@lru_cache(maxsize=1)
def style_catalog():
    path = Path(__file__).with_name('agents') / 'answer-style.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('version') != 1 or set(value['patterns']) != {'definition', 'situation', 'comparison', 'calculation', 'document'}:
        raise ValueError('Invalid answer-style catalog')
    return value


def style_guidance(domain, question):
    if domain not in {'legal', 'finance'}:
        return ''
    from claim_validation import question_type
    current = question.split('\nПредыдущий вопрос:')[0]
    kind = question_type(current)
    if kind == 'situation' and re.search(r'сравн|выгодн|выбрать между', current, re.I):
        kind = 'comparison'
    catalog = style_catalog()
    if kind not in catalog['enabled_patterns']:
        return ''
    pattern = catalog['patterns'][kind]
    example = pattern.get('examples', {}).get(domain, '')
    result = ('СТАНДАРТ ПОДАЧИ. Это образец формы, НЕ источник права, условий банка или фактов пользователя. '
              'Не переноси обстоятельства и ответы из примера в текущую ситуацию. '
              'Все фактические выводы по-прежнему подтверждай только источниками текущего запроса.\n'
              + '\n'.join(catalog['principles']) + '\nФорма текущего ответа: ' + pattern['shape'])
    if example:
        result += '\nПример только организационной части (не готовый ответ на текущий вопрос):\n' + example
    return result
