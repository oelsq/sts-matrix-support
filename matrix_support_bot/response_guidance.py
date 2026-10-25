"""Bounded help when retrieval or citation validation cannot support a claim.

These are organisational suggestions, never substitute legal rules or tariffs.
"""
import re


def is_document_request(question: str) -> bool:
    """The current turn asks for an actual draft, not another action checklist."""
    current = question.split("\nПредыдущий вопрос:")[0]
    # Describing coercion to sign/write is not a request for our draft.
    if re.search(r'застав|принужд|требует.{0,25}(?:напис|подпис)', current, re.I) and not re.search(
            r'помоги.{0,20}(?:напис|состав)|(?:напиши|составь|подготовь)|как (?:мне )?(?:написать|составить)', current, re.I):
        return False
    return bool(re.search(
        r"(?:напис|напиш|состав|сформулир|подготов|шаблон|черновик|образец|нужен\s+текст).*"
        r"(?:требован|заявлен|претензи|обращен|письм|жалоб)|"
        r"(?:требован|заявлен|претензи|обращен|письм|жалоб).*"
        r"(?:напис|напиш|состав|сформулир|подготов|шаблон|черновик|образец)", current, re.I))


def request_clarification(domain: str, question: str, previous_question: str = "") -> str | None:
    """Clarify genuinely missing subject; preserve scoped follow-ups and known bank names."""
    if domain not in {'legal','finance'}:
        return None
    label='Юридический агент' if domain=='legal' else 'Финансовый агент'
    current=question.split("\nПредыдущий вопрос:")[0].strip()
    if re.search(r'перепла[тч]',current,re.I) and re.search(r'рассчитай|посчитай|сколько|без график|только сумм|только.*ставк',current,re.I):
        from cashflow_calculator import TEMPLATE
        return (f'**{label}**\n\nПо одной сумме и ставке точную переплату определить нельзя: нужен полный график платежей, '
                'даты, фактически полученная сумма и отдельные расходы. Отправьте `!расчёт` или заполните шаблон ниже.\n\n'+TEMPLATE)
    if (re.search(r'страховщик|страховая|страхов[а-яё ]{0,15}выплат',question,re.I)
            and re.search(r'удерж|вычет|выч(?:[её]л|л[аи]|ита[а-яё]*)|расход.{0,25}оцен|оцен.{0,25}расход',current,re.I)
            and not re.search(r'дтп|каско|огпо|осаго|автострах|ответственност.{0,40}владельц',question,re.I)):
        return (f'**{label}**\n\nЗапросите у страховщика **письменный расчёт выплаты и отдельную расшифровку удержания**. '
                'Попросите указать, за какую услугу вычли деньги, кто понёс расходы и на какой пункт договора или норму ссылаются.\n\n'
                'Сохраните полис, уведомление о выплате и расчёт. Для проверки конкретного запрета удержания сначала нужно определить вид страхования; '
                'правило для одного полиса нельзя автоматически переносить на другой.\n\n'
                'Какой у вас полис: обязательная ответственность автовладельца, КАСКО, имущество или другой вид? '
                'Можно привести название и одну спорную строку без персональных данных.')
    generic_letter=re.fullmatch(
        r"(?:а\s+)?(?:как\s+(?:мне\s+)?(?:написать|составить|оформить)|(?:напиши|составь|подготовь))"
        r"\s+(?:мне\s+)?(?:требование|заявление|жалобу|претензию|письмо)[?!.\s]*",current,re.I)
    if generic_letter and not previous_question and '\nПредыдущий вопрос:' not in question:
        return (f'**{label}**\n\nКому адресовано обращение и что вы хотите получить: оплату за работу, '
                'возврат денег, исправление сведений или другое действие?\n\n'
                'Коротко опишите ситуацию без персональных данных — подготовлю текст с заполнителями.')
    if re.search(r'выдум|придум|сочини', current, re.I) and re.search(r'подтверд|выда[йт]|представ', current, re.I) and re.search(r'действующ|реальн|настоящ', current, re.I):
        return (f'**{label}**\n\nНе буду выдавать придуманную норму за действующий закон. '
                'Могу помочь проверить реальное положение или подготовить явно обозначенный учебный пример. '
                'Какой вопрос нужно решить на практике?')
    if re.search(r'российск.{0,20}(?:закон|кодекс)|закон.{0,15}(?:России|РФ)|работаю в России', current, re.I) and not re.search(r'Казахстан|РК|сравн', current, re.I):
        return (f'**{label}**\n\nВаш вопрос относится к России, а проверенная база этого помощника относится к Казахстану. '
                'Переносить казахстанские сроки на вашу ситуацию нельзя.\n\n'
                'Сохраните договор, условия оплаты и подтверждения работы. Если пришлёте нужный фрагмент российского '
                'документа без личных данных, помогу объяснить его содержание, отдельно обозначив пределы проверки.')
    if domain in {'legal','finance'}:
        from offline_knowledge import BANK_NAMES,is_tariff_question,is_rights_question
        if (any(name in question.casefold() for names in BANK_NAMES.values() for name in names)
                and is_tariff_question(question) and not is_rights_question(question)
                and not re.search(r'перевод|оплат|обслужив|сняти|пополн|выпуск|конверт|плат[её]ж',question,re.I)):
            return (f'**{label}**\n\nУточните продукт и операцию: например, перевод с карты, оплата услуги '
                    'или обслуживание счёта. По названию банка нельзя выбрать подходящую строку тарифа.')
        if re.fullmatch(r'(?:а\s+)?какая\s+комиссия\s+(?:в|у)\s+(?:freedom|фридом|halyk|халык|kaspi|каспи)[?!.\s]*',current,re.I):
            return (f'**{label}**\n\nУточните продукт и операцию: например, перевод с карты, оплата услуги '
                    'или обслуживание счёта. По одному названию банка нельзя выбрать подходящую строку тарифа.')
        from offline_knowledge import needs_bank
        if needs_bank(question):
            return (f'**{label}**\n\nУточните банк и продукт или приложение, через которое хотите провести операцию. '
                    'Например, Halyk SuperApp или карта Freedom. Комиссию нужно проверять по тарифу выбранного продукта.')
    return None


def retrieval_question(question: str, previous_question: str = "") -> str:
    # Short follow-ups need the user's topic, not an earlier generated answer
    # (which could contain a mistake). The current request remains first.
    # A new named bank or product must not inherit the preceding bank's tariff.
    bank_aliases = [("halyk", "халык"), ("freedom", "фридом"), ("kaspi", "каспи")]
    named = {i for i, names in enumerate(bank_aliases) if any(n in question.casefold() for n in names)}
    previous_named = {i for i, names in enumerate(bank_aliases) if any(n in previous_question.casefold() for n in names)}
    new_topic = bool(named and named != previous_named)
    if re.search(r"super\s*app", question, re.I) and not re.search(r"super\s*app", previous_question, re.I):
        new_topic = True
    # Recognise a new problem, while retaining context for the same problem.
    topics = [r"работодател|увольн|зарплат|заработн|отпуск|начальник", r"коллектор|чуж.*долг",
              r"банкрот", r"укра|похит|краж", r"арми|военкомат|призыв", r"\bкнб\b",
              r"алимент|развод|реб[её]нок", r"купил|неисправ|сломал.*телефон|магазин",
              r"средн.*зарплат|медиан", r"аренд|квартир|найм"]
    new_problem = any(re.search(pattern, question, re.I) and not re.search(pattern, previous_question, re.I)
                      for pattern in topics)
    fact_followup=bool(re.match(r'^(?:у меня |я работаю |работаю |)(?:договор (?:ГПХ|подряда|оказания услуг)|трудового договора нет|нет трудового договора|есть гарантия|гарантии нет)',question,re.I))
    followup = re.match(r"^(а\b|и\b|тогда\b|если\b|это\b|почему\b|какой\b|какие\b|сколько\b|что дальше|поясни|объясни|уточни)", question, re.I)
    if previous_question and len(question.split()) <= 18 and (followup or fact_followup or is_document_request(question)) and not new_topic and (not new_problem or fact_followup):
        return question + "\nПредыдущий вопрос: " + previous_question[:3000]
    return question


def is_reference_question(question: str) -> bool:
    """Identify short requests for a fact or overview, not an action plan."""
    q = question.split("\nПредыдущий вопрос:")[0].strip()
    if re.search(r"что (?:мне )?делать|как (?:мне )?(?:поступить|обжаловать|оформить)|помоги|угрож|срочно", q, re.I):
        return False
    return len(q.split()) <= 28 and bool(re.search(
        r"(?:со? )?скольк[иао].*лет|с какого возраста|что такое|что страхуется|что покрывает|что регулирует|чем отличается|кто такой|что (?:есть|знаешь).*про|про .+что есть|чем занимается|какие задачи|какая комиссия|минимальн.*продолжительност|какая доля|за какой срок|кому.*пеню|за сколько предупредить", q, re.I))


def salary_statistic_question(question: str) -> bool:
    q = question
    if re.search(r"рассчит|расч[её]т|начисл|среднеднев|среднечас|работник|отпускн|увольн|компенсац|сохранени", q, re.I):
        return False
    return bool(re.search(r"(?:средн|медиан).*?(?:зарплат|заработн)|(?:зарплат|заработн).*?(?:средн|медиан)", q, re.I))


def statistic_period(question: str):
    """Current turn overrides inherited period; no default to the current year."""
    current, _, previous = question.partition('\nПредыдущий вопрос:')
    def parse(text):
        year = re.search(r'\b(20\d{2})\b', text)
        quarter = re.search(r'\b(IV|III|II|I|[1-4])(?:[-– ]*(?:й|го|ый|ой))?\s*квартал', text, re.I)
        if quarter:
            token = quarter[1].upper()
            number = {'I':1,'II':2,'III':3,'IV':4}.get(token)
            number = number or int(token)
        else:
            named = re.search(r'\b(перв\w*|втор\w*|трет\w*|четв[её]рт\w*)\s+квартал', text, re.I)
            number = next((v for stem,v in [('перв',1),('втор',2),('трет',3),('четв',4)] if named and named[1].casefold().startswith(stem)), None)
        return (int(year[1]) if year else None, number)
    y,q=parse(current); py,pq=parse(previous)
    return y or py, q or pq


def statistic_territory(question: str):
    """Reject unmapped city/region requests instead of substituting national data.

    Prefer a location explicitly named this turn to earlier conversation turns.
    An unknown location is deliberately not mapped to another regional bulletin.
    """
    current, _, previous=question.partition('\nПредыдущий вопрос:')
    country={'казахстане','казахстан','казахстану','рк','стране'}
    non_locations={'этом','прошлом','следующем','первом','втором','третьем','четвертом',
                   'среднем','целом','статистике','бюллетене','документе','таблице','тенге','году','месяце','квартале'}
    def parse(text):
        country_named=False
        for m in re.finditer(r'\bв\s+(?:городе\s+)?([а-яё-]+)',text,re.I):
            place=m[1].casefold()
            if place in country or (place=='республике' and re.search('казахстан',text,re.I)):
                country_named=True
                continue
            if place not in non_locations:
                return 'unmapped:'+place
        if re.search(r'област|регион|район|город',text,re.I):
            return 'unmapped:regional'
        if country_named or re.search(r'Казахстан|\bРК\b|по всей стране',text,re.I):
            return 'KZ'
        return None
    return parse(current) or parse(previous)


def work_payment_question(question: str) -> bool:
    """Payment trouble, including inherited context; not salary statistics/calculation."""
    q = question.casefold().replace('ё', 'е')
    current = q.split('\nпредыдущий вопрос:')[0]
    if re.search(r'гпх|гражданск|подряд|оказани.{0,12}услуг', current):
        return False
    wages = re.search(r'жалақ|зарплат|заработн.{0,16}плат|начальник|ден[ье]г.{0,20}за работу', q)
    trouble = re.search(r'төлемей|төленбей|невыплат|не\s+(?:плат|выда|отда)|задерж|задолж|просроч|долг', q)
    from retrieval_intents import colloquial_work_payment
    return (bool(wages and trouble) or colloquial_work_payment(question)) and not salary_statistic_question(question)


def work_payment_draft() -> str:
    """Organisational letter example, without legal deadlines or personal data."""
    return (
            '**Черновик**\n'
            'Кому: [работодатель / руководитель организации]\n'
            'От: [имя]\n\n'
            '**Требование о выплате задолженности по заработной плате**\n\n'
            'По моим данным, мне не выплачена заработная плата за [период]. '
            'Предварительная сумма задолженности — [сумма] тенге.\n\n'
            'Прошу проверить расчёт и выплатить причитающуюся мне задолженность. '
            'Также прошу предоставить расчёт начислений и удержаний, '
            'а при задержке выплаты — расчёт причитающейся пени.\n\n'
            'Прошу письменно сообщить размер задолженности и срок её выплаты.\n\n'
            'Дата: [дата]\nПодпись: [подпись]\n\n'
            '**Как использовать**\n'
            'Заполните поля у себя, без отправки персональных данных в чат. Если сумма неизвестна, '
            'уберите предложение о предварительной сумме — просьба предоставить расчёт уже есть. '
            'Сохраните копию обращения и подтверждение его передачи работодателю.'
    )


def practical_fallback(domain: str, question: str, *, validation_failed: bool = False) -> str:
    label = "Юридический агент" if domain == "legal" else "Финансовый агент"
    q = question.casefold()
    from document_drafts import credit_payment_problem, credit_request_draft, consumer_refund_draft
    from document_drafts import rental_refund_request, is_rental_refund_draft
    if is_rental_refund_draft(question):
        return (f"**{label}**\n\nНиже редактируемый пример обращения о возврате платежа. "
                "Это предложение урегулировать вопрос по договору, а не вывод о том, что вся сумма подлежит возврату.\n\n" + rental_refund_request())
    if is_document_request(question) and credit_payment_problem(question) and not re.search(r'жалоб|иск|суд|омбуд|прокурат', question.split('\nПредыдущий вопрос:')[0], re.I):
        return (f"**{label}**\n\nНиже пример обращения с предложением пересмотреть платежи. "
                "Это редактируемый текст без неподтверждённых статей и сроков, а не обещание одобрения.\n\n"+credit_request_draft())
    if is_document_request(question) and re.search(r'телефон|смартфон',q) and re.search(r'неисправ|недостат|слома',q) and re.search(r'возврат|вернут|вернуть',q) and not re.search(r'ремонт|замен|жалоб|иск|суд|министр|омбуд',question.split('\nПредыдущий вопрос:')[0],re.I):
        return (f"**{label}**\n\nМожно подготовить такой пример претензии; конкретные права зависят от обстоятельств покупки.\n\n"+consumer_refund_draft())
    if is_document_request(question) and work_payment_question(question) and not re.search(r'жалоб|иск|суд|инспекц|прокурат',question.split('\nПредыдущий вопрос:')[0],re.I):
        return (f"**{label}**\n\nЕсли речь о зарплате по трудовым отношениям, можно начать с такого обращения. "
                "Это редактируемый пример без неподтверждённых статей и сроков.\n\n" + work_payment_draft())
    if salary_statistic_question(question):
        if validation_failed:
            return (f"**{label}**\n\nСтатистический материал найден, но итоговая цифра не прошла проверку подтверждений. "
                    "Не буду выдавать её за проверенное значение. Для ответа нужно сверить показатель, период и регион с таблицей выпуска.")
        return (f"**{label}**\n\nВ локальной базе пока нет подходящего статистического выпуска о средней или медианной зарплате. "
                "Это статистический показатель за определённый период; трудовой закон и банковские тарифы его не устанавливают.\n\n"
                "Нужен выпуск Бюро национальной статистики именно для запрошенного периода и территории. "
                "Данные другого квартала или всей страны нельзя подставлять за нужный показатель. "
                "Если у вас есть подходящая таблица, пришлите её заголовок и строку — помогу разобрать показатель и охват.")
    if re.search(r'переплат', q) and re.search(r'гэсв', q) and re.search(r'умнож|формул|счит|рассчит', q):
        return (f'**{label}**\n\nТочную переплату нельзя получить простым умножением суммы кредита на ГЭСВ и число лет. '
                'ГЭСВ — годовой показатель стоимости, а остаток долга и платежи меняются по графику.\n\n'
                'Для проверки выпишите все плановые платежи и обязательные расходы, уточнив, какие уже включены в платежи. '
                'Сравните их общую сумму с полученной суммой кредита, не учитывая один расход дважды. '
                'Пришлите обезличенный график — помогу проверить арифметику; результат будет расчётом по вашим данным, а не тарифом банка.')
    if domain=='finance' and not validation_failed:
        from offline_knowledge import is_tariff_question,needs_bank
        if is_tariff_question(question) and not needs_bank(question):
            topic=question.split("\nПредыдущий вопрос:")[0].strip()[:240]
            supplied_details=bool(re.search(r'gold|red|super\s*app|за\s|перевод|оплат|карт|вклад|депозит',question,re.I))
            followup=('В какой валюте, на какую сумму и через какой канал планируется операция?' if supplied_details
                      else 'Уточните название продукта и операцию, для которой нужно узнать комиссию.')
            return (f'**{label}**\n\nПо запросу «{topic}» в локальной базе пока нет проверенного тарифа для этой операции.\n\n'
                    'Пришлите соответствующую строку тарифа без реквизитов и персональных данных — помогу разобрать '
                    'комиссию, ограничения и сноски.\n\n'+followup)
    if validation_failed and is_reference_question(question):
        return (f"**{label}**\n\nДокумент по вашему вопросу найден, но подготовленный ответ не прошёл проверку подтверждений. "
                "Это ошибка проверки ответа, а не просьба заново назвать уже указанный банк или продукт. "
                "Непроверенную сумму или условие я не буду выдавать за факт.")
    if re.search(r"увольн|работодател|зарплат|заработн|отпуск|трудов", q):
        intro = "Начните с проверки документов и фиксации того, что произошло на работе."
        steps = ["Соберите трудовой договор, относящиеся к вопросу приказы, расчётные листки и переписку.",
                 "Запишите даты событий и ваш запрос: получить расчёт, объяснение или исправление.",
                 "Подготовьте письменный запрос работодателю и сохраните копию и подтверждение отправки."]
        followup = "Что произошло и когда: задержка выплаты, увольнение или другая ситуация?"
    elif re.search(r"товар|покуп|продав|гарант|возврат", q) and domain == "legal":
        intro = "Начните с описания проблемы и сохранения подтверждений покупки."
        steps = ["Подготовьте чек или другое подтверждение оплаты, фотографии недостатка и переписку.",
                 "Запишите дату покупки, обнаружения проблемы и обращения к продавцу.",
                 "Сформулируйте продавцу, что произошло и какого решения вы хотите, сохранив копию обращения."]
        followup = "Что за товар и когда вы его купили?"
    elif re.search(r"кредит|за[её]м|долг|просроч|реструктур", q):
        intro = "Сначала соберите данные по долгу: это поможет понять варианты и подготовить предметный запрос."
        steps = ["Сверьте договор, график и выписку: отдельно выпишите основной долг, проценты и спорные начисления.",
                 "Опишите причину проблемы и посильный для вас вариант платежей; не обещайте сумму, которую не сможете вносить.",
                 "Подготовьте обращение кредитору через известный официальный канал и сохраните ответ."]
        followup = "Это банк или МФО, и вопрос о просрочке, графике или спорном начислении?"
    elif re.search(r"комисси|тариф|ставк|депозит|вклад|карт|плат[её]ж|перевод", q):
        intro = "Для точного сравнения важны условия именно вашего продукта, а не похожего предложения другого банка."
        steps = ["Найдите название продукта и нужную операцию в договоре или сохранённом тарифе.",
                 "Сверьте валюту, сумму, канал операции и сноски: от них могут зависеть условия.",
                 "Пришлите сюда соответствующую строку без реквизитов и персональных данных — помогу её разобрать."]
        followup = "Какой банк и какой продукт или операция вас интересуют?"
        if re.search(r"halyk|халык|freedom|фридом|kaspi|каспи", q):
            followup = "Уточните название продукта, валюту и способ выполнения операции."
    elif re.search(r"аренд|квартир|договор|залог", q):
        intro = "Начните с условий договора и документов, которые подтверждают вашу позицию."
        steps = ["Найдите пункты об оплате, сроке, расторжении и возврате имущества или обеспечительного платежа.",
                 "Составьте хронологию событий, сохраните квитанции, акты и переписку.",
                 "Сформулируйте второй стороне конкретный письменный вопрос и желаемое решение."]
        followup = "Какое условие вызывает спор и чего вы хотите добиться?"
    else:
        # A factual question is not a dispute. Do not invent paperwork or
        # ask what "result" the user wants instead of admitting the exact gap.
        topic = question.split("\nПредыдущий вопрос:")[0].strip()[:240]
        if validation_failed:
            return (f"**{label}**\n\nПо вопросу «{topic}» нашлись материалы, но ответ не прошёл проверку подтверждений. "
                    "Я не буду выдавать непроверенные детали за установленный факт.\n\n"
                    "Можно сузить вопрос до одного пункта или прислать относящийся к нему текст нормы — разберу, что он подтверждает.")
        return (f"**{label}**\n\nВ локальной базе пока не найден источник по вопросу «{topic}». "
                "Поэтому сейчас я не могу надёжно назвать правило или условия.\n\n"
                "Если у вас есть текст соответствующего закона или документа, пришлите нужный фрагмент — помогу его объяснить. "
                "Повторять вопрос другими словами не требуется: нужно дополнить базу по этой теме.")
    return (f"**{label}**\n\n{intro}\n\n**Что можно подготовить сейчас**\n"
            + "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
            + "\n\nДля точного вывода по норме, сроку или условиям продукта пока недостаточно подходящих подтверждений в локальной базе. "
              "Перечисленные шаги — организационные советы, а не установленная законом процедура.\n\n"
            + followup)



def civil_payment_draft():
    """Organizational draft; no invented civil-law remedy, deadline or interest."""
    return ('**Требование заказчику — черновик**\n\n'
            'Это шаблон по сообщённому гражданскому договору. Перед отправкой сверьте факты и условия оплаты/приёмки; '
            'трудовые сроки и пеня автоматически здесь не применяются.\n\n'
            'Кому: [заказчик]\nОт: [исполнитель / подрядчик]\n\n'
            '**Требование об оплате по договору**\n\n'
            'Между нами заключён договор [вид, дата, номер]. По моим данным, '
            '[описание выполненных работ / оказанных услуг и обстоятельств их передачи заказчику]. '
            'Неоплаченная сумма по моему расчёту составляет [сумма и валюта] за [период / этап].\n\n'
            'Прошу проверить расчёт, сообщить о наличии разногласий по объёму и приёмке, '
            'оплатить причитающуюся сумму и письменно сообщить результат рассмотрения.\n\n'
            'Приложения: [договор, акты, подтверждения передачи результата, переписка — при наличии].\n'
            'Дата: [дата]\nПодпись: [подпись]\n\n'
            'Заполните поля у себя, уберите неподтверждённые сведения. Сохраните копию и подтверждение передачи; '
            'не отправляйте персональные данные и весь договор в общий чат.')
