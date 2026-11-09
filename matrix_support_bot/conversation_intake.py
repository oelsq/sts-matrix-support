"""Fast clarification of missing task/data; never invent a legal rule or rate."""
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation
import re

@dataclass(frozen=True)
class IntakeReply:
    answer: str
    mechanism: str

_SALARY=re.compile(r'зарплат|заработн.{0,12}плат|\bоклад\b|\bз\s*/?\s*п\b',re.I)
_PAY_PROBLEM=re.compile(r'не\s+(?:плат|выплат|отда)|невыплат|задолж|задерж|уволь|незакон|правомер|обжал|по закону|стать[яию]|положен|\bпен[яию]\b',re.I)
_CALC=re.compile(r'сколько|посчита|рассчита|расч[её]т|выходит|получ|на руки|чистыми|нетто|какая.{0,15}(?:у меня|моя|у нас)',re.I)
_NUMBER=r'(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,2})?'
_AMOUNT=_NUMBER+r'(?:\s*(?:тыс(?:яч[аиу]?)?\.?|млн\.?|миллион(?:а|ов)?))?(?:\s*(?:тенге|тг|kzt|₸|usd|доллар(?:а|ов)?|\$|eur|евро|€|rub|рубл(?:ей|я|ь)|₽))?'
_GROSS=re.compile(r'(?:начислен[оа]|начислили|начисленная сумма)\s*(?:всего\s*)?[:=—–]?\s*(?P<amount>'+_AMOUNT+r')(?!\d|[.,]\d)',re.I)
_DEDUCTION=re.compile(r'(?:удержано|удержали|удержания|вычли)\s*(?:всего\s*)?[:=—–]?\s*(?P<amount>'+_AMOUNT+r')(?!\d|[.,]\d)',re.I)

def _amount(pattern,text):
    matches=list(pattern.finditer(text))
    if len(matches)!=1:return None
    match=matches[0]
    if re.match(r'\s*(?:%|процент)',text[match.end():],re.I):return None
    # Reuse the same bounded Decimal/currency parser as the arithmetic tool.
    from arithmetic_reply import _TOKEN,_quantity
    token=_TOKEN.fullmatch(match['amount'].strip())
    return _quantity(token) if token else None

def _salary(domain,current,previous):
    context=current+'\n'+previous
    if _PAY_PROBLEM.search(current):return None
    explicit=bool(_SALARY.search(current) and _CALC.search(current))
    continuation=bool(_SALARY.search(previous) and _CALC.search(previous) and re.fullmatch(
        r'(?:а\s+)?(?:какие (?:данные|сведения)(?: тебе| вам)? нужны|что (?:тебе|вам) нужно|на руки|чистыми|нетто)[?.!\s]*',current,re.I))
    if not continuation and re.search(r'какие.{0,30}(?:данн|сведени)|что.{0,20}(?:прислать|сообщить)',current,re.I):return None
    amounts_request=bool(re.search(r'начислен|начислили',context,re.I) and re.search(r'удержан|удержали|вычли',context,re.I) and _CALC.search(current))
    if not (explicit or continuation or amounts_request):return None
    if re.search(r'средн|медиан|минимальн|по стран|по Казахстан',current,re.I):return None
    label='Юридический агент' if domain=='legal' else 'Финансовый агент'
    gross=_amount(_GROSS,current);deduct=_amount(_DEDUCTION,current)
    if gross is not None and deduct is not None and not re.search(r'%|процент',current,re.I):
        # Additional sums, dates or alternative calculations need clarification;
        # do not silently ignore an advance, bonus or another deduction.
        from arithmetic_reply import _TOKEN
        if len(list(_TOKEN.finditer(current))) != 2:return None
        if gross.unit and deduct.unit and gross.unit!=deduct.unit:
            answer='Начисления и удержания указаны в разных валютах. Уточните обе суммы в одной валюте; курс обмена я не буду придумывать.'
        elif gross.value<deduct.value:
            answer='Удержания больше начисленной суммы. Уточните, нет ли ошибки в цифрах и входит ли в удержания долг за другой период; сумму к выплате пока не буду угадывать.'
        else:
            from arithmetic_reply import _fmt
            unit=gross.unit or deduct.unit
            value=_fmt(gross.value-deduct.value,unit)[0]
            answer=(f'По вашим данным к выплате **{value}**.\n\n'
                    f'Расчёт: {_fmt(gross.value,unit)[0]} − {_fmt(deduct.value,unit)[0]} = {value}.\n\n'
                    'Это разница указанных начислений и **всех** удержаний; правильность самих налогов и удержаний здесь не устанавливается.')
        return IntakeReply(f'**{label}**\n\n'+answer,'salary_arithmetic')
    # A fully specified multi-step calculation or a percentage must go to its
    # calculator/model path; never replace it with an unrelated intake form.
    if re.search(r'\d',current) and re.search(r'%|процент|смен|час|\bдн\w*|отработ|преми',current,re.I):return None
    if len(current.split())>30:return None
    if gross is not None or (re.search(r'\d',current) and _SALARY.search(current)):
        answer=('Для суммы **на руки** нужно знать не только оклад, но и начисления за нужный период и удержания.\n\n'
                'Какая сумма фактически начислена и сколько всего удержано по расчётному листку? '
                'Если удержания ещё не известны, укажите месяц и год расчёта — ставки и льготы нельзя подставлять наугад.')
    else:
        answer=('Помогу посчитать зарплату. Какой у вас **оклад или начисленная сумма** и за какой период?\n\n'
                'Вас интересует начисление за месяц или сумма **на руки после удержаний**? '
                'Не присылайте ИИН, номера карт и другие личные данные — достаточно сумм и периода.')
    return IntakeReply(f'**{label}**\n\n'+answer,'salary_intake')

_ASSAULT=re.compile(r'драк|изби[лт]|избиени|побо[еиия]|напали|избит|подрал',re.I)
_INJURY=re.compile(r'больниц|госпитал|травм|пострада|изби[лт]|избит',re.I)
_LEGAL_DETAIL=re.compile(r'стать[яию]|уголовн|наказан|срок|компенсац|пособ|оплат|увол|выговор|винов|производственн|законн|правомер|страхов|полис|ответственност|покрыти|выплат|адвокат|\bсуд|обязан|выгна|прогул',re.I)

def _incident(domain,current,previous):
    followup=bool(_ASSAULT.search(previous) and re.search(r'перв\w*.{0,15}(?:действ|шаг)|с чего начать|что (?:сейчас |сначала )?делать',current,re.I))
    if not (bool(_ASSAULT.search(current) and _INJURY.search(current)) or followup):return None
    if _LEGAL_DETAIL.search(current) or len(current.split())>40:return None
    if re.search(r'драки не было|не было драки|никто не пострадал|травм нет',current,re.I):return None
    label='Юридический агент' if domain=='legal' else 'Финансовый агент'
    context=current+'\n'+previous
    outside=bool(re.search(r'после работы|вне работы|не на работе',context,re.I))
    at_work=not outside and bool(re.search(r'на работе|на рабочем месте|в офисе',context,re.I))
    employer=bool(re.search(r'я работодател|я руководител|я директор|мы работодател',context,re.I))
    place='' if outside or at_work else ' Драка произошла на работе или вне её?'
    role=('Нужно разобрать оформление отсутствия сотрудника или действия пострадавшего?' if employer else 'Вы обращаетесь как пострадавший, его коллега или работодатель?')
    if domain=='finance':
        answer=('Если сотрудник госпитализирован, сначала важны помощь и документы о случившемся. '
                'Финансовую часть можно разобрать отдельно: зарплату за отработанное время, возможные выплаты за период нетрудоспособности или расходы. '
                'Из самого факта драки сумму и право на выплату определить нельзя.\n\n'
                'Сохраните сведения о периоде отсутствия и обезличенные строки начислений; диагноз и медицинскую карту сюда не отправляйте.\n\n'
                'Что нужно выяснить — расчёт зарплаты или вопрос о больничном?'+place)
    else:
        answer=('Разберёмся. Если сотрудник уже в больнице, начните с **медицинской помощи и фиксации травм**, '
                'а не с предположений о виновности.\n\n'
                '**Что сделать сейчас**\n'
                '1. Сохраните медицинские документы и сведения о времени и месте происшествия.\n'
                '2. Сохраните записи, переписку и контакты свидетелей, если они есть; не распространяйте медицинские данные в общем чате.\n'
                '3. Если человека избили, можно подготовить обращение в полицию с описанием событий. При продолжающейся угрозе не ждите ответа бота и свяжитесь с экстренными службами.\n\n'+
                role+place+' '
                'Эти обстоятельства нужны, чтобы отдельно разобрать ответственность, отсутствие на работе и выплаты; статья и виновность по одному сообщению не определяются.')
    return IntakeReply(f'**{label}**\n\n'+answer,'incident_intake')

def intake_reply(domain,question,previous_question=''):
    if domain not in {'legal','finance'} or not isinstance(question,str):return None
    current=question.split('\nПредыдущий вопрос:')[0].strip()
    if not current or len(current)>2500:return None
    try:return _salary(domain,current,previous_question) or _incident(domain,current,previous_question)
    except (InvalidOperation,ValueError):return None
