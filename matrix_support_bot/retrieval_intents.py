"""Query vocabulary and article routing; these rules never generate legal claims.

Keep colloquial intent matching separate from the BM25 scoring loop. All boosts
refer to existing, integrity-checked documents, never external search results.
"""
import re

# pattern, query expansion, preferred documents, preferred article prefixes
RULES = [
    (r'(?:укра|краж|похит).*(?:телефон|смартфон)|(?:телефон|смартфон).*(?:укра|краж|похит)', 'кража тайное хищение чужого имущества', ('criminal',), ('Статья 188.',)),
    (r'брачн\w*.*договор|договор\w*.*брачн', 'брачный договор имущественные права супругов имущественное положение детей права обязанности в отношении детей', ('family',), ('Статья 39.','Статья 41.')),
    (r"(?=.*(?:гэ[сc]в|эффективн.*ставк))(?=.*(?:рассчита|посчита|вычисли|переплат)).+", "даты суммы график платежей расчет годовой эффективной ставки", ("gesv-rules-2026",), ("Пункт 11.", "Пункт 22.")),
    (r"гэ[сc]в|эффективн.*ставк|полная.*стоимост.*кредит", "годовая эффективная ставка расчет платежи включаются не включаются неустойка страхование комиссия", ("gesv-rules-2026",), ("Пункт 12.", "Пункт 14.", "Пункт 15.")),
    (r"(?s:(?:гэ[сc]в|эффективн.*ставк).*(?:страхов|комисси)|(?:страхов|комисси).*(?:гэ[сc]в|эффективн.*ставк))", "перечень комиссии страхование договор условия займа физическое лицо", ("bank-fees-2026",), ("Пункт 2.",)),
    (r"больничн|временн.*нетрудоспособ|еңбекке.*жарамсыз", "пособие по временной нетрудоспособности работодатель", ("labor",), ("Статья 133.",)),
    (r"наследств.*срок|срок.*наследств", "принятие наследства истечение срока", ("civil-special",), ("Статья 1072-2.", "Статья 1072-3.")),
    (r"наслед|завещан", "наследование принятие наследства", ("civil-special",), ()),
    (r"(?:номер.*телефон|телефон.*номер).*(?:переда|реклам)|(?:переда|реклам).*(?:номер|данн)|персональн.*данн", "персональные данные согласие сбор обработка защита права субъекта", ("personal-law",), ("Статья 7.", "Статья 8.", "Статья 24.")),
    (r"(?:телефон|смартфон|ноутбук|планшет|товар|холодильник).*(?:слом|ремонт|гаранти|неисправ|выш.*строя|чинить)|(?:слом|неисправ|ремонт|чинить).*(?:телефон|смартфон|ноутбук|планшет|холодильник)|магазин.*(?:не возвращ|возврат|отказ.*приним)|ақаулы.*тауар|тауар.*қайтар", "потребитель недостаток товара гарантийный срок возврат уплаченной суммы", ("consumer-law",), ("Статья 15.", "Статья 30.")),
    (r"^(?!.*(?:банк|ипотек|кредит|за[её]м|займ))(?:(?:аренд|хозяин|квартир).*(?:плат|повыс|подня|цен)|(?:плат|повыс|подня|цен).*аренд)", "арендная плата изменение размера договор имущественного найма", ("civil-special",), ("Статья 546.",)),
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
# New public statutes: route everyday vocabulary to the matching act/articles.
RULES += [
    (r"кредитн.*(?:истори|отч[её]т)|\bбки\b", "субъект кредитной истории недостоверная информация исправление кредитного отчета", ("credit-history",), ("Статья 22.", "Статья 31.")),
    (r"валютн.*(?:операци|контрол)|обмен.*валют", "валютная операция валютный контроль", ("currency-control",), ("Статья 1.",)),
    (r"(?:дтп.*страх|страх.*дтп|автострах|огпо.*авто|осаго)", "обязательное страхование ответственности владельцев транспортных средств страховой случай размер вреда", ("auto-insurance",), ("Статья 22.",)),
    (r"страхован.*работник|несчастн.*случа.*(?:работ|труд)|производственн.*травм", "обязательное страхование работника несчастный случай обязанности страхователя страховщика", ("employee-accident-insurance",), ()),
    (r"профсоюз", "профессиональный союз принадлежность работника запрет дискриминации", ("trade-unions",), ("Статья 4.", "Статья 7.")),
    (r"дольщик|долев.*(?:участи|строительств)|новостройк.*(?:покуп|деньг)|(?:покуп|купить).*новостройк", "долевое участие жилищное строительство информация уполномоченная компания способы привлечения денег", ("shared-construction",), ("Статья 7.", "Статья 22.")),
    (r"(?:прекращ|получ|нужн|требуется).*лицензи.*деятельност|лицензирован.*деятельност|разрешен.*деятельност|уведомлен.*начал.*деятельност", "разрешение уведомление лицензия порядок прекращения действия", ("permissions",), ()),
    (r"оператор.*связ|услуг.*связ|мобильн.*связ", "услуги связи абонент договор оператор", ("communications",), ()),
    (r"авторск|чуж.*(?:фотограф|фото)|служебн.*произведен", "авторское право имущественные права служебное произведение ограничения исключительных прав", ("copyright",), ("Статья 16.",)),
    (r"бесплатн.*юридическ.*помощ|юридическ.*помощ.*бесплатн", "гарантированная государством юридическая помощь правовое информирование консультирование защита представительство", ("legal-aid",), ("Статья 26.",)),
]
# Combine subject and action cues; a generic word (paper, debt, intermediary)
# alone must not route a legal question to an unrelated statute.
RULES += [
    (r"^(?=.*(?:рисунк|эскиз|иллюстрац|картин|произведен))(?=.*(?:копир|копии|воспроизвод|печат|перенес|перен[её]с|размест|использ|прода|продаж))(?=.*(?:мо[йеёюия]|сво[йеёюия]|без.{0,30}(?:разреш|соглас)|автор)).+", "автор произведения исключительное право воспроизведение распространение защита", ("copyright",), ("Статья 16.", "Статья 49.")),
    (r"^(?=.*(?:госорган|государственн|госуслуг|цон))(?=.*(?:справк|бумаг|документ|сведени|информац))(?=.*(?:требу|прос[яи]|принес|принест|предостав))(?=.*(?:систем|баз|располага|уже.{0,30}(?:есть|получ))).+", "государственная услуга сведения информационные системы запрещается истребование документов", ("state-services",), ("Статья 5.", "Статья 19.")),
    (r"^(?=.*(?:спор|конфликт|примир|улад|урегулир))(?=.*(?:посредник|примирител|нейтральн|независим.{0,30}(?:лиц|посредник)|треть.{0,20}лиц))(?=.*(?:без.{0,30}суд|не.{0,30}суд|примир|соглаш|урегулир|разреш)).+", "медиация добровольность посредник медиатор договор соглашение об урегулировании спора", ("mediation",), ("Статья 4.", "Статья 21.", "Статья 27.")),
    (r"^(?=.*(?:телефон|адрес|фамили|персональн|фотограф|мо[её].{0,15}фото))(?=.*(?:сайт|интернет|публичн|канал|распростран|опубликов|вылож|размест))(?=.*(?:без.{0,35}(?:соглас|разреш)|не.{0,20}(?:давал|согласовывал)|мо[июйеё])).+", "персональные данные распространение согласие субъекта защита права", ("personal-law",), ("Статья 7.", "Статья 15.", "Статья 24.")),
    (r"^(?=.*(?:оборудован|станок|станк|автомобил|имуществ))(?=.*(?:использ|пользова|владен|бер[её]м|предлага))(?=.*(?:выкуп|передач.{0,25}собственност))(?=.*(?:плат|взнос))(?!.{0,250}без.{0,12}выкуп).+", "финансовый лизинг лизингополучатель лизингодатель предмет платежи выкуп", ("leasing",), ("Статья 2.", "Статья 15.")),
    (r"^(?=.*(?:квартир|жиль|недвижим|дом\b))(?=.*(?:залог|обеспечен))(?=.*(?:кредит|за[её]м|займ|жиль[её].{0,25}обеспечен|обеспечен.{0,25}квартир)).+", "ипотека недвижимое имущество залог обязательство ипотечный договор", ("mortgage",), ("Статья 1.", "Статья 7.")),
    (r"^(?=.*(?:дол[гж]|за[её]мщик|обязательств))(?=.*(?:не.{0,15}(?:мож|тян)|нет.{0,15}средств|неплат[её]ж|неспособ))(?=.*(?:восстанов|списан|списать|несостоятельн|банкрот)).+", "восстановление платежеспособности банкротство граждан судебная внесудебная процедура условия", ("bankruptcy",), ("Статья 5.", "Статья 6.")),
    (r"^(?=.*(?:акци[йяиюе]|акционерн))(?=.*(?:дивиденд|распределен.{0,15}прибыл|выплат.{0,12}доход)).+", "акционерное общество дивиденды обыкновенные привилегированные акции решение собрания ограничения выплаты", ("joint-stock",), ("Статья 22.", "Статья 23.", "Статья 24.")),
    (r"цифров[а-яё ]{0,35}актив|актив[а-яё ]{0,20}цифров", "цифровые финансовые активы обеспеченные необеспеченные определение виды", ("digital-assets",), ("Статья 1.", "Статья 5.")),
    (r"^(?=.*(?:бюро|кредитн.{0,20}(?:истори|отч[её]т)))(?=.*(?:погаш|закрыт|ошиб|недостовер|неверн|исправ|оспор|просроч)).+", "субъект кредитной истории поставщик информации оспаривание недостоверной информации исправление отчета", ("credit-history",), ("Статья 22.", "Статья 31.")),
    (r"^(?=.*(?:квартир|жиль|дом))(?=.*(?:будущ|строящ|стройк|новострой|застройщик|котлован|до.{0,20}(?:сдач|окончан)))(?=.*(?:деньг|ден[еь]г|оплат|переда|привлеч|документ|покуп)).+", "долевое участие жилищное строительство способы привлечения денег гарантия разрешение информация", ("shared-construction",), ("Статья 7.", "Статья 18.", "Статья 22.")),
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

def colloquial_work_payment(question):
    current=question.casefold().split('\nпредыдущий вопрос:')[0]
    if re.search(r'гпх|подряд|оказани.{0,12}услуг',current):return False
    work=re.search(r'зарплат|заработн.{0,16}плат|работодател|начальник|увол|сократ|сокращен|заработанн.{0,20}ден|ден.{0,20}заработанн|(?:выполненн.{0,10}|за )работ',question,re.I)
    trouble=re.search(r'невыплат|не\s+(?:плат|выплат|отда|выда|получ|перечисл|поступ|оплат)|задерж|задолж|ден[ье]г.{0,25}нет',question,re.I)
    return bool(work and trouble)


def query_hints(question):
    expansions, documents, sections = [], set(), {}
    image_sections = image_recording_sections(question)
    if image_sections:
        expansions.append('биометрические персональные данные распознавание идентификация лица согласие обработка' if biometric_image_question(question) else 'право на собственное изображение фотография видеосъемка получение распространение информации частная жизнь согласие личные семейные нужды')
        documents.update(image_sections)
        for document, headings in image_sections.items():
            sections.setdefault(document, []).extend(headings)
    for pattern, expansion, docs, headings in RULES:
        if pattern.search(question):
            expansions.append(expansion)
            documents.update(docs)
            if headings:
                for doc in docs:
                    sections.setdefault(doc, []).extend(headings)
    if consumer_defect_remedy_question(question):
        expansions.append('возможный недостаток товара причина повреждения требования потребителя проверка качества отличие от товара надлежащего качества')
        documents.add('consumer-law');sections.setdefault('consumer-law',[]).extend(['Статья 15.','Статья 30.'])
    if consumer_quality_return_question(question):
        expansions.append('обмен возврат непродовольственного товара надлежащего качества не был в употреблении товарный вид потребительские свойства')
        documents.add('consumer-law');sections.setdefault('consumer-law',[]).extend(['Статья 14.','Статья 30.'])
    if deposit_guarantee_question(question):
        expansions.append('гарантируемый депозит предел возмещения сберегательные иные иностранная валюта совокупное банк участник')
        documents.add('deposit-guarantee');sections.setdefault('deposit-guarantee',[]).append('Статья 18.')
    if auto_liability_definition(question):
        expansions.append('объект обязательного страхования ответственности имущественный интерес вред третьим лицам')
        documents.add('auto-insurance');sections.setdefault('auto-insurance',[]).extend(['Статья 3.','Статья 22.'])
    if collector_contact_question(question):
        expansions.append('коллекторское агентство третье лицо угрозы насилие контактные данные должника')
        documents.add('collectors');sections.setdefault('collectors',[]).append('Статья 5.')
    if child_residence_question(question):
        expansions.append('место жительства ребенка мнение ребенка интересы соглашение родителей возраст десяти лет')
        documents.add('family');sections.setdefault('family',[]).extend(['Статья 62.','Статья 73.'])
    if consent_withdrawal_question(question):
        expansions.append('отзыв согласия обработка персональных данных противоречит законам неисполненное обязательство')
        documents.add('personal-law');sections.setdefault('personal-law',[]).append('Статья 8.')
    if re.search(r'трудов.{0,20}договор',question,re.I) and re.search(r'гпх|подряд',question,re.I) and re.search(r'отлич|разниц|сравн',question,re.I):
        expansions.append('отличительные признаки трудового договора трудовая функция личное выполнение подчинение трудовому распорядку')
        documents.add('labor');sections.setdefault('labor',[]).append('Статья 27.')
    if minor_employment_question(question):
        expansions.append('заключение трудового договора несовершеннолетний возраст согласие ограничения продолжительность рабочего времени')
        documents.add('labor');sections.setdefault('labor',[]).extend(['Статья 31.','Статья 26.','Статья 69.'])
    if colloquial_work_payment(question):
        expansions.append('работодатель заработная плата задолженность дата выплаты работнику')
        documents.add('labor');sections.setdefault('labor',[]).append('Статья 113.')
    if re.search(r'увол|сократ|сокращен|прекращен.{0,20}трудов', question, re.I):
        expansions.append('прекращение трудового договора акт работодателя выплата причитающихся сумм сокращение')
        documents.add('labor'); sections.setdefault('labor', []).extend(['Статья 61.', 'Статья 113.'])
    if re.search(r'начисл|на руки|удержан|расч[её]тн.{0,10}лист', question, re.I) and re.search(r'трудов|зарплат|заработн', question, re.I):
        expansions.append('заработная плата начисления удержания индивидуальный подоходный налог налоговые вычеты')
        documents.update({'labor', 'tax', 'social'})
        sections.setdefault('labor', []).extend(['Статья 113.', 'Статья 115.'])
    if re.search(r'НДС|добавленн.{0,15}стоимост', question, re.I):
        expansions.append('дата совершения оборота по реализации товаров работ услуг ставка налога')
        documents.add('tax'); sections.setdefault('tax', []).extend(['Статья 460.', 'Статья 503.'])
    if re.search(r'КАСКО|добровольн.{0,30}страхован', question, re.I):
        expansions.append('условия договора страхования основания отказа в осуществлении страховой выплаты')
        documents.add('civil-special'); sections.setdefault('civil-special', []).extend(['Статья 825.', 'Статья 839.'])
    if re.search(r'квартир|жиль|недвижим|ипотек', question, re.I) and re.search(r'залог|ипотек|взыскан', question, re.I) and re.search(r'банк|кредит|за[её]м|ипотек', question, re.I):
        expansions.append('ипотека залог недвижимого имущества обращение взыскания уведомление залогодателя')
        documents.add('mortgage'); sections.setdefault('mortgage', []).extend(['Статья 1.', 'Статья 20.', 'Статья 24.'])
    if mortgage_ownership_question(question):
        expansions.append('способы реализации ипотеки обращение в свою собственность заложенного имущества торги несостоявшиеся')
        documents.add('mortgage');sections['mortgage']=['Статья 20.','Статья 1.','Статья 32.']+sections.get('mortgage',[])
    from term_definitions import definition_term
    term = definition_term(question)
    if term == 'gesv':
        expansions.append('годовая эффективная ставка вознаграждения реальная стоимость понятия услуг банка')
        documents.add('gesv-term-guide')
        sections['gesv-rules-2026'] = ['Пункт 1.'] if re.search(r'вклад|депозит', question, re.I) else ['Пункт 1.','Пункт 12.','Пункт 15.']
    gph_overview = term == 'gph' or bool(re.search(r'\bгпх\b|гражданско[ -]правов',question,re.I)
                        and re.search(r'что такое|что означает|что значит|расшифр|отлич|разниц|сравн',question,re.I))
    if gph_overview:
        expansions.append('гражданско правовой договор соглашение установление изменение прекращение гражданских прав обязанностей')
        documents.add('gph-term-guide')
        documents.add('civil-general');sections.setdefault('civil-general',[]).extend(['Статья 378.','Статья 380.'])
        documents.add('civil-special');sections.setdefault('civil-special',[]).extend(['Статья 616.','Статья 683.'])
    if re.search(r'договор\w*\s+подряда',question,re.I) or (re.search(r'гпх',question,re.I) and not gph_overview):
        expansions.append('договор подряда заказчик подрядчик приемка выполненной работы оплата работы')
        documents.add('civil-special');sections.setdefault('civil-special',[]).extend(['Статья 616.','Статья 623.','Статья 630.'])
    if re.search(r'договор\w*\s+(?:на\s+)?оказани\w*\s+услуг',question,re.I):
        expansions.append('возмездное оказание услуг заказчик оплата услуг исполнитель')
        documents.add('civil-special');sections.setdefault('civil-special',[]).extend(['Статья 683.','Статья 685.'])
    if re.search(r'отпуск.{0,35}за свой сч[её]т|за свой сч[её]т.{0,35}отпуск',question,re.I):
        expansions.append('отпуск без сохранения заработной платы соглашение сторон заявление работника')
        documents.add('labor');sections.setdefault('labor',[]).append('Статья 97.')
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


LOAN_STATUS_NOTIFICATION = re.compile(
    r"(?:уведомлен|сообщен|смс|sms|ссылк|статус).*за(?:[её]|й)м|за(?:[её]|й)м.*(?:сообщен|уведомлен|ссылк)", re.I)


def loan_status_notification(question):
    """A status message needs source verification before treating it as a debt."""
    return bool(LOAN_STATUS_NOTIFICATION.search(question))


def general_legal_aid_question(question):
    """Recognize public eligibility, without overriding criminal-defence procedure."""
    if re.search(r'задержан|задержали|допрос|подозреваем|обвиняем',question,re.I):return False
    return bool(re.search(r'бесплатн|гарантированн',question,re.I)
                and re.search(r'юридическ|правов|адвокат|защитник',question,re.I)
                and re.search(r'кто|кому|всем|право|получить|категори|положен',question,re.I))


def child_residence_question(question):
    if re.search(r'реб[её]н|дет[ьи]',question,re.I) and re.search(r'с кем|прожив|мест.{0,12}жительств|выбор|выбир|сам реш|самостоятельн',question,re.I):
        return True
    if not re.search(r'\b(?:дочь|дочер[а-яё]*|дочк[а-яё]*|сын[а-яё]*)\b',question,re.I):
        return False
    if re.search(r'совершеннолет|взросл',question,re.I):return False
    age=re.search(r'(?:дочери|дочке|сыну).{0,10}?(\d{1,2})\s*(?:лет|год)',question,re.I)
    if age and int(age[1])>=18:return False
    if re.search(r'кредит|ипотек|долг|работ|професси|университет|школ|колледж',question,re.I):return False
    residence=re.search(r'жить|жив[её]т|прожив|мест.{0,12}жительств',question,re.I)
    dispute=re.search(r'спор|выбор|выбир|реш|развод|(?:мать|отец|родител[ьи]).{0,12}против',question,re.I)
    return bool(residence and dispute)


def consent_withdrawal_question(question):
    return bool(re.search(r'отозв|отзыв|забра.{0,20}согласи|прекрат.{0,20}обработ',question,re.I) and re.search(r'согласи|персональн',question,re.I))


def deposit_guarantee_question(question):
    return bool(re.search(r'вклад|депозит',question,re.I) and re.search(r'гарант|страхован|возмещ|лиш.{0,15}лиценз|вернут полностью',question,re.I))


def auto_liability_definition(question):
    return bool(re.search(r'автострах|огпо|осаго|страхован.{0,35}ответственност.{0,30}автомобил',question,re.I)
                and re.search(r'покрыва|страхуется|страхование своей|объект',question,re.I)
                and not re.search(r'ремонт|оценщик|удерж|выплат|отказ|как получить|что делать',question,re.I))


def collector_contact_question(question):
    return bool(re.search(r'коллектор|взыскател.{0,15}долг|звон.{0,20}чуж.{0,10}долг',question,re.I))


def minor_employment_question(question):
    """A minor asking about employment, not any financial product or contract."""
    q=question.casefold().split('\nпредыдущий вопрос:')[0]
    if re.search(r'кредит|за[её]м|ипотек|вклад|депозит|страх|алимент|брачн|гпх|подряд',q):return False
    ages=[int(m[1]) for m in re.finditer(r'(?<!\d)(\d{1,2})\s*(?:лет|год)',q)]
    minor=any(12<=age<18 for age in ages) or bool(re.search(r'несовершеннолет|подрост',q))
    work=re.search(r'подработ|трудов|трудоустро|работодател|на работу|работать',q)
    action=re.search(r'договор|подпис|устро|работать|при[её]м|наним',q)
    return bool(minor and work and action)


# Ordinary photography is not biometric identification. These helpers only
# select source sections; they never assert a permission or a legal exception.
def image_recording_question(question):
    q = question.casefold().replace('ё', 'е')
    photo = bool(re.search(r'фото|видео|с[ъь]емк|камер', q))
    portrait = bool(re.search(r'изображени[ея].{0,25}(?:челов|лиц|граждан)|собственн.{0,12}изображени|чуж.{0,12}изображени', q))
    context = bool(re.search(r'люд|человек|прохож|лиц|полици|полице|охран|соглас|публик|распростран|вылож|выклад|опубликов|магазин|ценник|товар|улиц|парк|обществен|биометр|распознаван|авторск|частн.{0,15}жизн', q))
    return portrait or (photo and context) or bool(re.search(r'(?:снимать|снимаю|снял|сняли|записывать).{0,45}(?:людей|человек|прохож|полице|полици|охранник|улиц|общественн|ценник|товар)', q))



def biometric_image_question(question):
    q = question.casefold().replace('ё', 'е')
    # An explicit absence of recognition must not turn a phone video into a
    # question about an operator's face-identification equipment.
    q = re.sub(r'без (?:автоматического )?распознавания лиц|не использую (?:биометрию|распознавание лиц)', '', q)
    return image_recording_question(question) and bool(re.search(r'биометр|распознаван|идентификаци.{0,20}(?:лиц|личност)', q))


def image_recording_sections(question):
    if not image_recording_question(question):
        return {}
    if biometric_image_question(question):
        return {'personal-law': ('Статья 1.', 'Статья 3.', 'Статья 7.', 'Статья 8.'),
                'constitution': ('Статья 21',)}
    sections = {'civil-general': ('Статья 144.', 'Статья 145.'),
                'constitution': ('Статья 21', 'Статья 23'),
                'online-platforms': ('Статья 18.',),
                'personal-law': ('Статья 3.',)}
    if re.search(r'магазин|торгов|товар|ценник|продав|потребител|кафе|ресторан', question, re.I):
        sections['consumer-law'] = ('Статья 24.',)
    if re.search(r'персональн|обработк.{0,15}данн', question, re.I):
        sections['personal-law'] = ('Статья 3.', 'Статья 7.', 'Статья 15.')
    return sections


def ordinary_image_scope(question):
    if not image_recording_question(question) or biometric_image_question(question):
        return False
    # Keep specialised/mixed cases open to their own procedural sources.
    return not re.search(r'судебн|заседани|допрос|следствен|военн|режимн|гостайн|государственн.{0,10}секрет|медицин|больниц|трудов|зарплат|налог|кредит|аренд|авторск|товарн.{0,12}знак|логотип', question, re.I)


def consumer_quality_return_question(question):
    """Change-of-mind/used but sound goods; never settle a disputed defect."""
    q=question.casefold().replace('ё','е')
    if consumer_reported_defect(q):return False
    if re.search(r'кредит|депозит|вклад|облигац|ценн.{0,12}бумаг|акци[юи]',q):return False
    retail=re.search(r'магазин|продав|покуп|\bкупил[аи]?|товар|куртк|пальто|одежд|обув|ботин|туфл|кроссов|плать|рубаш|костюм',q)
    returning=re.search(r'верн|возврат|обмен|поменя|не подош|разонрав',q)
    no_defect=re.search(r'без (?:каких[- ]либо )?(?:дефект|недостат)|дефектов нет|недостатков нет',q)
    sound=re.search(r'(?<!не)исправ|(?<!не)надлежащ|носил|носила|ношен|пользовал|не подош|размер|расцвет|фасон|разонрав',q)
    fault=re.search(r'неисправ|ненадлежащ|слом|брак|порвал|дефект|недостат',q)
    return bool(retail and returning and (sound or no_defect) and (not fault or no_defect))


def mortgage_ownership_question(question):
    q=question.casefold().replace('ё','е')
    return bool(re.search(r'ипотек|залог',q) and re.search(r'квартир|жиль|недвижим|\bдом\b',q)
                and re.search(r'собственност|принадлеж|стал.{0,20}владельц',q))


def consumer_reported_defect(question):
    """A reported symptom guides search; it never proves a manufacturing defect."""
    q=question.casefold().replace('ё','е')
    if not re.search(r'товар|покуп|продав|магазин|ботин|обув|кроссов|куртк|пальто|телефон|смартфон|ноутбук|холодильник|планшет',q):return False
    q=re.sub(r'без (?:каких[- ]либо )?(?:недостатк|дефект)\w*|(?:недостатк|дефект)\w* нет',' ',q)
    return bool(re.search(r'раскле|откле|тр[её]снул|порвал|оторвал|шов.{0,20}разош|разош.{0,20}шов|перестал.{0,15}работ|перестал.{0,15}включ|не (?:включа|работа)|слом|неисправ|недостат|дефект|\bбрак(?:ом|а|е|у)?\b',q))


def consumer_defect_remedy_question(question):
    return (consumer_reported_defect(question) and not image_recording_question(question)
            and bool(re.search(r'возврат|верн|обмен|ремонт|гарант|претенз|отказ|требова|тот же случай|что делать',question,re.I)))


def required_source_anchors(question):
    """Bounded coverage for explicit multi-part questions, not new legal claims."""
    anchors=[]
    if consumer_defect_remedy_question(question):
        anchors.extend([('consumer-law','Статья 15.',''),('consumer-law','Статья 30.','не был в употреблении')])
    if (image_recording_question(question) and not biometric_image_question(question)
            and re.search(r'магазин|ценник|продав|товар|потребител',question,re.I)
            and re.search(r'публи|распростран|вылож|соцсет',question,re.I)):
        anchors.extend([('consumer-law','Статья 24.','фото'),('civil-general','Статья 145.','изображени')])
    if re.search(r'КАСКО',question,re.I) and re.search(r'ОГПО|ОСАГО|обязательн.{0,30}ответственност',question,re.I):
        anchors.extend([('auto-insurance','Статья 3.','третьих лиц'),('civil-special','Статья 839.','')])
    return list(dict.fromkeys(anchors))
