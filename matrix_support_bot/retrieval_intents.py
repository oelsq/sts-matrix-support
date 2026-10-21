"""Query vocabulary and article routing; these rules never generate legal claims.

Keep colloquial intent matching separate from the BM25 scoring loop. All boosts
refer to existing, integrity-checked documents, never external search results.
"""
import re

# pattern, query expansion, preferred documents, preferred article prefixes
RULES = [
    (r"больничн|временн.*нетрудоспособ|еңбекке.*жарамсыз", "пособие по временной нетрудоспособности работодатель", ("labor",), ("Статья 133.",)),
    (r"наследств.*срок|срок.*наследств", "принятие наследства истечение срока", ("civil-special",), ("Статья 1072-2.", "Статья 1072-3.")),
    (r"наслед|завещан", "наследование принятие наследства", ("civil-special",), ()),
    (r"(?:номер.*телефон|телефон.*номер).*(?:переда|реклам)|(?:переда|реклам).*(?:номер|данн)|персональн.*данн", "персональные данные согласие сбор обработка защита права субъекта", ("personal-law",), ("Статья 7.", "Статья 8.", "Статья 24.")),
    (r"(?:телефон|смартфон|ноутбук|планшет|товар|холодильник).*(?:слом|ремонт|гаранти|неисправ|выш.*строя|чинить)|(?:слом|неисправ|ремонт|чинить).*(?:телефон|смартфон|ноутбук|планшет|холодильник)|магазин.*(?:не возвращ|возврат|отказ.*приним)|ақаулы.*тауар|тауар.*қайтар", "потребитель недостаток товара гарантийный срок возврат уплаченной суммы", ("consumer-law",), ("Статья 15.", "Статья 30.")),
    (r"(?:аренд|хозяин|квартир).*(?:плат|повыс|подня|цен)|(?:плат|повыс|подня|цен).*аренд", "арендная плата изменение размера договор имущественного найма", ("civil-special",), ("Статья 546.",)),
    (r"регистр.*(?:прав|собственност|недвижим)|(?:прав|собственност|недвижим).*регистр", "государственная регистрация прав на недвижимое имущество", ("real-estate-registration",), ()),
    (r"(?:погас|погаш|верну).*(?:раньше срока|досроч)|досроч.*(?:погас|погаш|кредит|заем|заём)", "досрочное погашение банковского займа вознаграждение неустойка", ("banks", "loan-early-repayment-guide"), ()),
    (r"(?=.*(?:кредит|за[её]м|плат[её]ж))(?=.*(?:(?:упал|снизил|потерял|уменьш).*доход|доход.*(?:сниз|упал|уменьш)|не тяну|не хватает.*денег|трудн.*плат)).+", "реструктуризация задолженности финансовые трудности изменение условий займа", ("regulator-restructure",), ()),
    (r"(?:мфо|банк).*(?:не хочет|отказ|не соглас).*(?:график|услов|реструктур)|отказ.*реструктур", "отказ в реструктуризации порядок обращения омбудсман", ("regulator-refusal", "microfinance"), ()),
    (r"(?:потерял|потерян|утер|украл).*(?:банковск.*карт|карт.*банк)|(?:просит|просят|запрашива).*?(?:смс|sms|cvv|cvc|парол)|(?:смс|sms)[ -]*код.*(?:просит|просят)", "мошенничество банковские карты блокировка скомпрометированных карт SMS код", ("nb-fraud", "nb-faq", "nb-card-safe-account"), ()),
    (r"семейн.*бюджет|личн.*бюджет|финансов.*подуш|резерв.*расход|отбасылық.*бюджет", "семейный бюджет доходы расходы финансовый резерв накопления финансовая цель", ("family-budget-guide",), ()),
    (r"(?:как|стоит).*?(?:выбра|выбир|сравни)\w*.*(?:вклад|депозит)|сравни\w*.*(?:вклад|депозит)", "выбор депозита ликвидность срок сбережения валюта цель", ("deposit-choice-guide",), ()),
    (r"несколько кредит|кредит.*(?:трех|трёх|двух|нескольк).*банк|единый график|коллективн.*урегулирован", "коллективное урегулирование задолженности несколько кредиторов единый график", ("collective-debt-guide",), ()),
    (r"добровольн.*пенсионн|дпв\b|ерікті.*зейнетақы", "добровольные пенсионные взносы размер периодичность", ("enpf-voluntary-2026", "social"), ()),
]
RULES = [(re.compile(pattern, re.I), expansion, docs, sections)
         for pattern, expansion, docs, sections in RULES]

CODE_NAMES = [
    (re.compile(r"трудов.*кодекс|\bтк\b", re.I), "labor"),
    (re.compile(r"гражданск.*кодекс.*общ|\bгк\b.*общ", re.I), "civil-general"),
    (re.compile(r"гражданск.*кодекс.*особ|\bгк\b.*особ", re.I), "civil-special"),
    (re.compile(r"налогов.*кодекс|\bнк\b", re.I), "tax"),
    (re.compile(r"уголовно[ -]процессуальн|\bупк\b", re.I), "criminal-procedure"),
    (re.compile(r"уголовн.*кодекс|\bук\b", re.I), "criminal"),
    (re.compile(r"гражданск.*процессуальн|\bгпк\b", re.I), "civil-procedure"),
    (re.compile(r"\bкоап\b|кодекс.*административн.*правонаруш", re.I), "administrative-offences"),
]

def query_hints(question):
    expansions, documents, sections = [], set(), {}
    for pattern, expansion, docs, headings in RULES:
        if pattern.search(question):
            expansions.append(expansion)
            documents.update(docs)
            if headings:
                for doc in docs:
                    sections.setdefault(doc, []).extend(headings)
    return " ".join(expansions), documents, sections


def explicit_article(question):
    """Only resolve an article when the act is unambiguously named."""
    match = re.search(r"\b(?:статья|статьи|статью|ст\.)\s*(\d+(?:-\d+)?)\b", question, re.I)
    if not match:
        return None
    documents = {doc for pattern, doc in CODE_NAMES if pattern.search(question)}
    # The shorter 'уголовный' name must not override УПК.
    if "criminal-procedure" in documents:
        documents.discard("criminal")
    return (documents.pop(), "Статья " + match[1] + ".") if len(documents) == 1 else None
