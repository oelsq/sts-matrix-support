"""Source-bound explanations for specific factual and interpretation regressions.

Only use them when the defining source clauses are present. They are summaries
of the supplied edition, not an assessment of an individual dispute.
"""
import re
from agents import render


def checked_legal_answer(question, evidence, *, domain="legal"):
    # Earlier questions explain the subject, not the current user's intent.
    # Do not repeat a canned age/refund explanation when the user asks what next.
    from response_guidance import is_document_request
    if is_document_request(question):
        return None
    q = question.split("\nПредыдущий вопрос:")[0].casefold()
    if (re.search(r"без оформлен|без договора|без трудов\w* договора|не оформля", q)
            and not re.search(r"рассчит|компенсац|отпуск|увол|пен[яию]|иск|суд", q)):
        rule = next((p for p in evidence if p['document_id']=='labor'
                     and str(p.get('section','')).startswith('Статья 33.')
                     and 'по вине работодателя' in ' '.join(str(p['text']).split())
                     and 'считаются возникшими с того дня, когда работник приступил к работе' in ' '.join(str(p['text']).split())), None)
        if rule:
            # Point 3 concerns missing paperwork; point 4 concerns an invalidated contract.
            # Do not transfer the separate compensation list from point 4 to this case.
            text = re.split(r"(?:^|\n)\s*4\. Признание", str(rule['text']), maxsplit=1)[0]
            return render(domain,
                'Отсутствие оформленного договора само по себе не означает, что у вас нет трудовых прав. '
                'Если договор отсутствует или оформлен ненадлежащим образом **по вине работодателя**, '
                'статья 33 считает трудовые отношения возникшими со дня, когда работник приступил к работе. [S1]\n\n'
                '**Что можно сделать сейчас**\n'
                '1. Запишите, когда начали работу, какие обязанности выполняли и кто давал задания.\n'
                '2. Сохраните переписку, подтверждения выполненной работы и выплат. Это поможет объяснить обстоятельства.\n'
                '3. Письменно попросите работодателя оформить отношения и сообщить расчёт оплаты. Сохраните копию обращения.\n\n'
                'Эта норма не определяет автоматически размер задолженности или все положенные выплаты. '
                'Для этого нужно отдельно разобраться в фактах и применимых положениях.\n\n'
                'Работу организовывали как для сотрудника или вы договорились выполнить отдельную услугу?',
                question, [{**rule, 'text':text}])
    if re.search(r"арми|военкомат|призыв|срочн.*служб", q) and re.search(r"скольк[иао].*лет|с какого возраста|призывной возраст", q):
        rule = next((p for p in evidence if p['document_id']=='military-service'
                     and str(p['section']).startswith('Статья 31.')
                     and 'от восемнадцати до двадцати семи лет' in ' '.join(str(p['text']).split())
                     and 'не имеющие права на отсрочку или освобождение' in ' '.join(str(p['text']).split())), None)
        if rule:
            return render(domain,
                'В Казахстане на **срочную воинскую службу призывают с 18 лет**. Статья 31 устанавливает возраст '
                'от 18 до 27 лет для граждан, не имеющих права на отсрочку или освобождение от призыва. [S1]\n\n'
                'Сам по себе возраст ещё не означает, что человека обязательно призовут: основания для отсрочки и освобождения тоже учитываются. [S1]',
                question, [rule])
    if re.search(r"телефон|смартфон", q) and re.search(r"неисправ|недостат|слома", q):
        definition = next((p for p in evidence if p['document_id']=='consumer-law'
                           and 'относятся исключительно транспортные средства' in ' '.join(str(p['text']).split())), None)
        rights = next((p for p in evidence if p['document_id']=='consumer-law'
                       and str(p['section']).startswith('Статья 15.')
                       and 'недостатки не были оговорены' in ' '.join(str(p['text']).split())
                       and 'расторжения договора и возврата' in str(p['text'])), None)
        if definition and rights:
            answer = (
                'Если телефон продан с недостатком, который продавец не оговорил, возврат денег — одно из требований, '
                'которые потребитель может выбрать по статье 15. Это ещё не означает автоматического удовлетворения требования: нужно разобраться в обстоятельствах неисправности. [S2]\n\n'
                '**Что сделать сейчас**\n'
                '1. Сохраните чек или другое подтверждение покупки, гарантийные документы и переписку.\n'
                '2. Опишите, что не работает и когда обнаружили проблему; по возможности сохраните фото или видео.\n'
                '3. Подготовьте письменное обращение продавцу: опишите недостаток и укажите, что просите расторгнуть договор и вернуть деньги. Сохраните подтверждение передачи.\n'
                '4. Если продавец отказывает, попросите письменное объяснение причины и документы о результатах проверки товара.\n\n'
                '**Важное различие**\n'
                'В загруженной редакции закона телефон не включён в закрытый перечень технически сложных товаров: '
                'в нём перечислены транспортные средства, сельскохозяйственная техника, водные и воздушные суда. '
                'Поэтому ограничение возврата только существенным недостатком для этой категории нельзя автоматически переносить на телефон. [S1]\n\n'
                'Когда вы купили телефон и какой именно недостаток обнаружили? Это поможет проверить сроки и обстоятельства требования.'
            )
            return render(domain, answer, question, [definition, rights])
    if re.search(r"арендодател|аренд|найм|квартир", q) and re.search(r"залог|депозит|взнос|обеспечительн", q) and re.search(r"возвра|вернут|вернуть|удерж", q):
        rule = next((p for p in evidence if p['document_id']=='civil-general'
                     and str(p['section']).startswith('Статья 338-4.')
                     and 'по вине плательщика' in str(p['text'])
                     and 'по вине получателя' in str(p['text'])
                     and 'по соглашению сторон' in str(p['text'])), None)
        if rule:
            answer = (
                'Начните с условий обеспечительного платежа и причины удержания. Само слово «залог» в переписке ещё не определяет, какие правила применяются к вашей сумме.\n\n'
                '**Что проверить сейчас**\n'
                '1. Найдите в договоре назначение платежа, условия его возврата, допустимые удержания и порядок завершения аренды. Важно содержание условия, а не только его название.\n'
                '2. Попросите арендодателя письменно объяснить удержание и представить расчёт: какие долги или повреждения он указывает и чем подтверждает.\n'
                '3. Соберите подтверждение оплаты, акт возврата квартиры, фотографии её состояния и переписку.\n'
                '4. Подготовьте письменное требование о возврате, указав сумму и соответствующий пункт договора. Сохраните подтверждение отправки.\n\n'
                '**Что говорит закон**\n'
                'Если платёж юридически является гарантийным взносом, статья 338-4 различает случаи неисполнения: '
                'при вине плательщика взнос остаётся у другой стороны; при вине получателя, прекращении обязательства по соглашению сторон '
                'или невозможности исполнения без вины сторон — возвращается. Это условное правило, а не вывод о том, кто прав в вашей аренде. [S1]\n\n'
                'Как в договоре описано назначение платежа и почему арендодатель отказывается его возвращать?'
            )
            return render(domain, answer, question, [rule])
    return None


def checked_work_payment_answer(domain, question, evidence):
    """A narrow source-bound guide/draft, never a substitute for all labour advice."""
    from response_guidance import is_document_request, work_payment_question, work_payment_draft
    current = question.split("\nПредыдущий вопрос:")[0]
    if domain not in {'legal', 'finance'} or not work_payment_question(question):
        return None
    # A service contract or dismissal requires different analysis. Don't turn
    # an inherited wage topic into an answer to a different current question.
    if re.search(r'гпх|гражданск|подряд|оказани.{0,12}услуг|увол|прекращ|расторж', question, re.I):
        return None
    drafting = is_document_request(current)
    if drafting and re.search(r'жалоб|иск|суд|инспекц|прокурат',current,re.I):
        return None
    if not drafting and (not work_payment_question(current) or
            re.search(r'рассчит|расч[её]т|посчит|пен[яюи]|неустой|стать|сколько|срок|дней|куда|жалоб|инспекц|отказ|суд', current, re.I)):
        return None
    def normalized(p): return ' '.join(str(p['text']).split()).casefold()
    payment = next((p for p in evidence if p['document_id']=='labor'
                    and str(p.get('section','')).startswith('Статья 113.')
                    and 'дата выплаты заработной платы' in normalized(p)
                    and 'предусматривается трудовым, коллективным договорами' in normalized(p)), None)
    arrears = next((p for p in evidence if p['document_id']=='labor'
                    and str(p.get('section','')).startswith('Статья 113.')
                    and 'работодатель выплачивает работнику задолженность и пеню' in normalized(p)), None)
    if not payment or not arrears:
        return None
    if drafting:
        answer = (
            'Ниже редактируемый черновик **для случая трудовых отношений**. '
            'При задержке зарплаты сохранённая норма предусматривает выплату работодателем работнику задолженности и пени. [S2]\n\n'
            + work_payment_draft()
        )
    else:
        answer = (
            '**Если это зарплата по трудовым отношениям**, начните с фиксации невыплаты и письменного требования работодателю. '
            'Дата выплаты зарплаты закрепляется в трудовом или коллективном договоре. [S1] '
            'При задержке норма предусматривает выплату работодателем работнику задолженности и пени. [S2]\n\n'
            '**Что сделать сейчас**\n'
            '1. Запишите, за какой период не заплатили, какая сумма ожидается и когда должна была быть выплата.\n'
            '2. Сохраните договор, расчётные листки, подтверждения выполненной работы, выписку о поступлениях и переписку.\n'
            '3. Письменно попросите работодателя проверить расчёт, выплатить задолженность и сообщить срок выплаты. '
            'Сохраните копию обращения и подтверждение передачи.\n\n'
            'Могу подготовить текст: напишите **А как написать требование?**.\n\n'
            'У вас трудовой договор или договор на оказание услуг? От этого зависит дальнейший порядок действий.'
        )
    return render(domain, answer, question, [payment, arrears])


def checked_practical_guidance(domain, question, evidence):
    """Bounded explanations/drafts only when the actual reviewed text is present."""
    from response_guidance import is_document_request
    from document_drafts import credit_payment_problem, credit_request_draft, consumer_refund_draft
    current = question.split('\nПредыдущий вопрос:')[0]
    drafting = is_document_request(current)
    if domain not in {'legal','finance'}:
        return None
    q = question.casefold()
    from response_guidance import work_payment_question
    if not drafting and work_payment_question(question) and re.search(r'куда|жалоб|жалова|инспекц',current,re.I):
        guide=next((p for p in evidence if p['document_id']=='labor-wage-guide'
                    and 'государственную инспекцию труда' in str(p['text'])
                    and 'работник вправе обратиться к работодателю' in str(p['text'])),None)
        if guide:
            answer=('**Если речь о зарплате по трудовым отношениям**, официальная памятка указывает возможность '
                    'обратиться к работодателю и в государственную инспекцию труда, в том числе через «e-Өтініш». [S1]\n\n'
                    '**Что подготовить**\n'
                    '1. Запишите период невыплаты, ожидаемую сумму, даты и сведения о работодателе.\n'
                    '2. Сохраните договор, расчётные листки, подтверждения работы и переписку.\n'
                    '3. В обращении коротко опишите невыплату и попросите проверить ситуацию; приложите подходящие подтверждения.\n'
                    '4. Сохраните копию и подтверждение подачи.\n\n'
                    'Памятка также указывает на выплату задолженности и пени при задержке. [S1] '
                    'Точный расчёт и дальнейшая процедура зависят от обстоятельств. '
                    'У вас трудовой договор или договор на оказание услуг?')
            return render(domain,answer,question,[guide])
    # A phone claim must not inherit an unsupported "substantial defect" rule.
    if drafting and re.search(r'телефон|смартфон', q) and re.search(r'неисправ|недостат|слома', q) and re.search(r'возврат|вернут|вернуть', q) and not re.search(r'ремонт|замен|скид|уменьшен|жалоб|иск|суд|министр|омбуд', current, re.I):
        definition = next((p for p in evidence if p['document_id']=='consumer-law' and 'относятся исключительно транспортные средства' in ' '.join(str(p['text']).split())), None)
        rights = next((p for p in evidence if p['document_id']=='consumer-law' and str(p.get('section','')).startswith('Статья 15.') and 'недостатки не были оговорены' in ' '.join(str(p['text']).split()) and 'расторжения договора и возврата' in str(p['text'])), None)
        if definition and rights:
            answer = ('Ниже пример претензии. Если продавец не оговорил недостаток, возврат оплаты — одно из требований по сохранённой статье 15. [S2] '
                      'Телефон не включён в закрытый перечень технически сложных товаров этой редакции: '
                      'условие о существенном недостатке для такой категории нельзя автоматически переносить на телефон. [S1] '
                      'Причины неисправности и обстоятельства покупки ещё нужно проверить.\n\n'+consumer_refund_draft())
            return render(domain,answer,question,[definition,rights])
    if not credit_payment_problem(question):
        return None
    if re.search(r'отказ',current,re.I) and re.search(r'реструктур',current,re.I) and not drafting:
        guide=next((p for p in evidence if p['document_id']=='regulator-refusal' and 'банковскому омбудсману' in str(p['text']) and 'микрофинансовому омбудсману' in str(p['text'])),None)
        if guide:
            answer=('После отказа в реструктуризации официальная памятка указывает возможность обратиться '
                    'к **банковскому омбудсману по банковскому кредиту** или **микрофинансовому омбудсману по займу МФО**. [S1]\n\n'
                    '**Что подготовить**\n'
                    '1. Сохраните своё обращение кредитору, подтверждение подачи и полученный ответ.\n'
                    '2. Соберите договор, график и краткое описание причины финансовых трудностей.\n'
                    '3. Опишите, с чем не согласны и какое изменение платежей предлагаете.\n\n'
                    'Это подготовка к обращению, а не гарантия изменения договора. '
                    'Бот не подаёт заявление и не проверяет его статус.')
            return render(domain,answer,question,[guide])
    guide=next((p for p in evidence if p['document_id']=='regulator-restructure' and 'заявлением о реструктуризации долга' in str(p['text']) and 'не вправе взимать плату за рассмотрение заявления' in str(p['text'])),None)
    if not guide:
        return None
    if drafting and re.search(r'жалоб|иск|суд|омбуд|прокурат',current,re.I):
        return None
    if drafting:
        answer=('Можно обратиться к кредитору с предложением изменить условия: '
                'официальная памятка советует указать причины трудностей и предлагаемый вариант. '
                'Согласие кредитора не гарантировано. [S1]\n\n'+credit_request_draft())
        return render(domain,answer,question,[guide])
    if not re.search(r'нечем.*плат|нет денег.*кредит|снизить.*плат[её]ж',current,re.I):
        return None
    if re.search(r'рассчит|посчит|сколько|срок|банкрот|суд|мошенн|неустой',current,re.I):
        return None
    answer=('Можно **попросить реструктуризацию** — изменение условий погашения. '
            'Официальная памятка перечисляет возможные варианты: отсрочка, увеличение срока и снижение ставки. '
            'Это варианты для рассмотрения кредитором, а не обещание одобрения или списания долга. [S1]\n\n'
            '**Что сделать сейчас**\n'
            '1. Выпишите текущий платёж, доход, обязательные расходы и посильную сумму платежа.\n'
            '2. Подготовьте обращение: причина трудностей, подтверждающие сведения и предлагаемое изменение.\n'
            '3. Попросите новый график и полную стоимость: меньший ежемесячный платёж сам по себе не означает меньшую переплату.\n\n'
            'По памятке банк и МФО не вправе брать плату за рассмотрение заявления о реструктуризации. [S1]\n\n'
            'Могу подготовить текст — напишите **А напиши заявление в банк**. '
            'У вас банковский кредит или микрокредит МФО?')
    return render(domain,answer,question,[guide])
