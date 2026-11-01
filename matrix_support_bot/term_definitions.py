"""Narrow term intents and source-backed definition anchors shared by both agents.

The snippets explain only definitions. They cannot establish a user's contract,
calculate a rate, replace an MFO rule or create a new legal entitlement.
"""
from dataclasses import dataclass
import re


def norm(text):
    return ' '.join(str(text).casefold().translate(str.maketrans('‐‑‒–—', '-----')).split())


def definition_term(question):
    current = question.split('\nПредыдущий вопрос:')[0].strip()
    q = norm(current)
    if len(q) > 650 or re.search(r'\d|[%=+*/]', q):
        return None
    terms = [name for name, pattern in [
        ('gph', r'\bгпх\b|гражданско[ -]правов\w*\s+характер'),
        ('gesv', r'\bгэ[сc]в\b|годов\w*\s+эффективн\w*\s+ставк'),
    ] if re.search(pattern, q)]
    if len(terms) != 1:
        return None
    # A short definition must never swallow an additional practical/legal task.
    if re.search(r'рассчита|рассчит|посчита|вычисл|переплат|пен[яию]|неусто|штраф|'
                 r'налог|взнос|страхов|комисси|включ|учитыв|номинальн|отлич|разниц|сравн|'
                 r'выбрат|выгодн|не\s+(?:плат|оплат)|задерж|удерж|обяз|вправе|'
                 r'каки\w*.{0,25}прав|право|срок|претензи|жалоб|заявлен|напиши|составь|увол|должен|должна|'
                 r'сколько|когда|почему|можно\s+ли|может\s+ли|могут\s+ли|'
                 r'что.{0,15}делать|как.{0,20}(?:поступить|обжалов|оформ|платить)|'
                 r'работаю|заключил|подписал|\bмфо\b|микрокредит|kaspi|halyk|freedom', q):
        return None
    asks_meaning = bool(re.search(r'что\s+(?:такое|означает|значит)|это\s+что|'
                                 r'расшифр|объясни|поясни|простыми словами|человеческим языком|термин', q))
    bare = re.fullmatch(r'(?:гпх|гэ[сc]в)[?!.\s]*', q)
    return terms[0] if asks_meaning or bare else None


GPH_ALIAS = 'договор гражданско-правового характера (далее - ГПХ) регулируется Гражданским кодексом Республики Казахстан'
CONTRACT = 'Договором признается соглашение двух или нескольких лиц об установлении, изменении или прекращении гражданских прав и обязанностей'
APR_ALIAS = 'Расчет годовой эффективной ставки вознаграждения (ГЭСВ) по займу'
APR_MEANING = ('годовая эффективная ставка вознаграждения - ставка вознаграждения в достоверном, '
               'годовом, эффективном, сопоставимом исчислении (реальная стоимость) в отношении услуг банка')
APR_COSTS = ('В расчет годовой эффективной ставки вознаграждения по займу включаются платежи клиента '
             'по основному долгу и вознаграждению, а также комиссии и иные платежи')
APR_EXCLUSION = ('В расчет годовой эффективной ставки вознаграждения по займу не включаются',
                 'платежи клиента, связанные с несоблюдением им условий договора банковского займа, включая неустойку и иные виды штрафных санкций')


@dataclass(frozen=True)
class DefinitionPart:
    name: str
    passage: dict
    needles: tuple[str, ...]
    sentence: str


def definition_parts(question, evidence):
    term = definition_term(question)
    if term is None:
        return []

    def part(name, document, section, needles, sentence):
        if isinstance(needles, str):
            needles = (needles,)
        source = next((p for p in evidence if p.get('document_id') == document
                       and str(p.get('section', '')).startswith(section)
                       and all(norm(n) in norm(p.get('text', '')) for n in needles)), None)
        return DefinitionPart(name, source, needles, sentence) if source is not None else None

    if term == 'gph':
        required = [
            part('расшифровка ГПХ', 'gph-term-guide', '', GPH_ALIAS,
                 'ГПХ — договор гражданско-правового характера.'),
            part('понятие договора', 'civil-general', 'Статья 378.', CONTRACT,
                 'Это соглашение двух или нескольких лиц о гражданских правах и обязанностях: их установлении, изменении или прекращении.'),
        ]
        return required if all(required) else []

    required = [
        part('расшифровка ГЭСВ', 'gesv-term-guide', '', APR_ALIAS,
             'ГЭСВ — годовая эффективная ставка вознаграждения.'),
        part('значение для банковской услуги', 'gesv-rules-2026', 'Пункт 1.', APR_MEANING,
             'В банковских правилах это показатель реальной стоимости услуги в пересчёте на год.'),
    ]
    if not all(required):
        return []
    if not re.search(r'вклад|депозит', question, re.I):
        costs = part('платежи по банковскому займу', 'gesv-rules-2026', 'Пункт 12.', APR_COSTS,
                     'По банковскому займу учитываются предусмотренные правилами платежи по основному долгу, вознаграждению и включаемые комиссии.')
        excluded = part('исключение для нарушения договора', 'gesv-rules-2026', 'Пункт 15.', APR_EXCLUSION,
                        'Неустойка и штрафы за нарушение договора в этот расчёт не включаются.')
        if costs and excluded:
            required.extend([costs, excluded])
    return required


def checked_term_definition(domain, question, evidence):
    parts = definition_parts(question, evidence)
    if domain not in {'legal', 'finance'} or not parts:
        return None
    from agents import render
    sources = [p.passage for p in parts]
    answer = '\n\n'.join(f'{p.sentence[:-1]} [S{i}].' for i, p in enumerate(parts, 1))
    return render(domain, answer, question, sources)


def definition_focus(question, evidence, sources):
    """Map each essential definition part to its exact source and Q locators.

    A negative heading and its list item may occupy different excerpts. Keep
    both; the item mentioning a penalty alone does not prove its exclusion.
    """
    parts = definition_parts(question, evidence)
    selected, plan = [], []
    for part in parts:
        index = next(i for i, p in enumerate(evidence) if p is part.passage)
        source = sources[index]
        excerpts = source['excerpts']
        combined = ' '.join(norm(e['quote']) for e in excerpts)
        spans, pos = [], 0
        for e in excerpts:
            length = len(norm(e['quote']))
            spans.append((pos, pos + length, e))
            pos += length + 1
        needed = []
        for needle in part.needles:
            begin = combined.find(norm(needle))
            if begin < 0:
                break
            end = begin + len(norm(needle))
            needed.extend(e for start, stop, e in spans if start < end and stop > begin)
        else:
            ids = list(dict.fromkeys(e['id'] for e in needed))
            plan.append({'part': part.name, 'source_id': source['id'], 'quote_ids': ids})
            for e in needed:
                item = {'source_id': source['id'], 'quote_id': e['id'], 'quote': e['quote']}
                if item not in selected:
                    selected.append(item)
    return plan, selected
