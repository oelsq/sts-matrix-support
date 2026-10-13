"""Bounded help when retrieval or citation validation cannot support a claim.

These are organisational suggestions, never substitute legal rules or tariffs.
"""
import re


def is_document_request(question: str) -> bool:
    """The current turn asks for an actual draft, not another action checklist."""
    current = question.split("\nПредыдущий вопрос:")[0]
    return bool(re.search(
        r"(?:напис|напиш|состав|сформулир|подготов|шаблон|черновик|образец).*"
        r"(?:требован|заявлен|претензи|обращен|письм|жалоб)|"
        r"(?:требован|заявлен|претензи|обращен|письм|жалоб).*"
        r"(?:напис|напиш|состав|сформулир|подготов|шаблон|черновик|образец)", current, re.I))


def request_clarification(domain: str, question: str, previous_question: str = "") -> str | None:
    """Clarify genuinely missing subject; preserve scoped follow-ups and known bank names."""
    if domain not in {'legal','finance'}:
        return None
    label='Юридический агент' if domain=='legal' else 'Финансовый агент'
    current=question.split("\nПредыдущий вопрос:")[0].strip()
    generic_letter=re.fullmatch(
        r"(?:а\s+)?(?:как\s+(?:мне\s+)?(?:написать|составить|оформить)|(?:напиши|составь|подготовь))"
        r"\s+(?:мне\s+)?(?:требование|заявление|жалобу|претензию|письмо)[?!.\s]*",current,re.I)
    if generic_letter and not previous_question and '\nПредыдущий вопрос:' not in question:
        return (f'**{label}**\n\nКому адресовано обращение и что вы хотите получить: оплату за работу, '
                'возврат денег, исправление сведений или другое действие?\n\n'
                'Коротко опишите ситуацию без персональных данных — подготовлю текст с заполнителями.')
    if domain=='finance':
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
    followup = re.match(r"^(а\b|и\b|тогда\b|если\b|это\b|почему\b|какой\b|какие\b|сколько\b|что дальше|поясни|объясни|уточни)", question, re.I)
    if previous_question and len(question.split()) <= 18 and (followup or is_document_request(question)) and not new_topic and not new_problem:
        return question + "\nПредыдущий вопрос: " + previous_question[:3000]
    return question


def is_reference_question(question: str) -> bool:
    """Identify short requests for a fact or overview, not an action plan."""
    q = question.split("\nПредыдущий вопрос:")[0].strip()
    if re.search(r"что (?:мне )?делать|как (?:мне )?(?:поступить|обжаловать|оформить)|помоги|угрож|срочно", q, re.I):
        return False
    return len(q.split()) <= 24 and bool(re.search(
        r"(?:со? )?скольк[иао].*лет|с какого возраста|что такое|кто такой|что (?:есть|знаешь).*про|про .+что есть|чем занимается|какие задачи|какая комиссия", q, re.I))


def salary_statistic_question(question: str) -> bool:
    q = question.split("\nПредыдущий вопрос:")[0]
    if re.search(r"рассчит|расч[её]т|начисл|среднеднев|среднечас|работник|отпускн|увольн|компенсац|сохранени", q, re.I):
        return False
    return bool(re.search(r"(?:средн|медиан).*?(?:зарплат|заработн)|(?:зарплат|заработн).*?(?:средн|медиан)", q, re.I))


def work_payment_question(question: str) -> bool:
    """Payment trouble, including inherited context; not salary statistics/calculation."""
    q = question.casefold().replace('ё', 'е')
    current = q.split('\nпредыдущий вопрос:')[0]
    if re.search(r'гпх|гражданск|подряд|оказани.{0,12}услуг', current):
        return False
    wages = re.search(r'зарплат|заработн.{0,16}плат|начальник|ден[ье]г.{0,20}за работу', q)
    trouble = re.search(r'невыплат|не\s+(?:плат|выда|отда)|задерж|задолж|просроч|долг', q)
    return bool(wages and trouble) and not salary_statistic_question(question)


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
                "Для точного ответа нужен выпуск Бюро национальной статистики с периодом и регионом. "
                "Вас интересует средняя или медианная зарплата, по всей стране или по конкретному региону?")
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
