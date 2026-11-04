"""Bounded Decimal arithmetic on user inputs, separate from legal assertions."""
import ast
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
import re


class ArithmeticInputError(ValueError):
    pass


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str = ""


_NUMBER = r"(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,6})?"
_SCALE = r"тыс(?:яч[аиу]?)?\.?|тысяч(?:а|и|у)?|млн\.?|миллион(?:а|ов)?|млрд\.?|миллиард(?:а|ов)?"
_CURRENCY = r"тенге|теңге|тг|kzt|₸|usd|доллар(?:а|ов)?|\$|eur|евро|€|rub|рубл(?:ь|я|ей)|₽"
_TOKEN = re.compile(r"(?<![\w.,])(?P<number>"+_NUMBER+r")"
    r"(?:\s*(?P<scale>"+_SCALE+r"))?"
    r"(?:\s*(?P<unit>"+_CURRENCY+r"))?"
    r"(?:\s*(?P<percent>%|процент(?:а|ов)?))?(?!\w|[.,]\d)", re.I)
_NORMATIVE = re.compile(r"законн|правомер|обязан|должен ли|должна ли|име\w* ли право|"
    r"по закону|согласно|разрешено ли|какая ставка|какой налог|"
    r"сколько положено|стать[яию]|\bгэсв\b|годовых|сложны[ех] процент|"
    r"капитализац|аннуитет|ежемесячн|ежедневн|ежегодн|что (?:мне )?делать|"
    r"куда обращ|как оспор|как вернуть|составь|напиши|заявлен|претензи|жалоб|"
    r"имею ли право|может ли",re.I)
_PREFIX = re.compile(r"^(?:(?:пожалуйста|посчитай|посчитайте|рассчитай|рассчитайте|вычисли|"
    r"вычислите|сколько будет|сколько|чему равно|чему равняется|а|и)\s+)+")


def _quantity(match):
    value=Decimal(match['number'].replace(' ','').replace('\u00a0','').replace('\u202f','').replace(',','.'))
    scale=(match['scale'] or '').casefold()
    if scale:
        value *= Decimal('1000000000' if scale.startswith(('млрд','миллиард')) else
                         '1000000' if scale.startswith(('млн','миллион')) else '1000')
    if abs(value)>Decimal('1000000000000000'):
        raise ArithmeticInputError('Число слишком большое для этого калькулятора.')
    unit=(match['unit'] or '').casefold()
    if unit:
        unit=('тенге' if unit in {'тенге','теңге','тг','kzt','₸'} else
              'USD' if unit in {'usd','$','доллар','доллара','долларов'} else
              'EUR' if unit in {'eur','евро','€'} else 'RUB')
    if match['percent'] and (scale or unit):
        raise ArithmeticInputError('Укажите процент отдельно от суммы и валюты.')
    if unit and value!=value.quantize(Decimal('.01')):
        raise ArithmeticInputError('Укажите денежную сумму с точностью до двух знаков после запятой.')
    return Quantity(value,unit)


def _fmt(value, unit=''):
    places=Decimal('.01') if unit else Decimal('.000001')
    rounded=value.quantize(places,rounding=ROUND_HALF_UP)
    if rounded==0:rounded=abs(rounded)
    number=f'{rounded:,.6f}'.replace(',',' ').rstrip('0').rstrip('.').replace('.',',')
    return number+(' '+unit if unit else ''),rounded!=value


def _shown(q):
    return _fmt(q.value,q.unit)[0]


def _percent(question):
    matches=list(_TOKEN.finditer(question))
    percentages=[m for m in matches if m['percent']]
    amounts=[m for m in matches if not m['percent']]
    if len(percentages)!=1 or len(amounts)!=1:
        return None
    if not re.search(r'сколько|какая\s+(?:вся\s+)?сумм|посчита|рассчита|вычисл|найди|\bот\b|прибав|добав|выч|'
                     r'удерж|увелич|уменьш|скидк|наценк|остат|остан',question):return None
    if re.search(r'[*/]|раздел|подел|делить',question):return None
    rate=_quantity(percentages[0]);base=_quantity(amounts[0])
    # A sign attached to a literal must not silently become positive.
    if any(question[:m.start()].rstrip().endswith('-') for m in matches):
        return None
    if re.search(r'не\s+(?:(?:надо|нужно)\s+)?(?:прибав|добав|выч|удерж|увелич|уменьш)',question):
        return None
    implied_part=(amounts[0].end()<percentages[0].start() and
                  re.fullmatch(r'\s*(?:это|составляет)\s*',question[amounts[0].end():percentages[0].start()]))
    if implied_part or re.search(r'от какой|какой суммы|вся сумма|вс[ею] сумму|полная сумма|исходн\w* сумм|сколько всего',question):
        if rate.value==0:
            raise ArithmeticInputError('По нулевому проценту нельзя однозначно восстановить исходную сумму. Укажите ненулевой процент.')
        total=Quantity(base.value*100/rate.value,base.unit)
        shown,rounded=_fmt(total.value,total.unit)
        answer=(f'Если {_shown(base)} — это {_shown(rate)}% суммы, то вся сумма: **{shown}**.'
                f'\n\nРасчёт: {_shown(base)} × 100 ÷ {_shown(rate)} = {shown}.')
        if re.search(r'остат|остан',question):
            rest=Quantity(total.value-base.value,base.unit)
            answer+=f'\n\nОстальная часть: **{_shown(rest)}** ({shown} − {_shown(base)}).'
        return answer+ ('\n\nРезультат округлён.' if rounded else '')
    part=Quantity(base.value*rate.value/100,base.unit)
    remaining=bool(re.search(r'остан\w*|остат\w*|после\s+(?:удержан|вычет|скидк)|'
        r'выч\w*|удерж\w*|уменьш\w*|скидк\w*|минус|меньше|сниз\w*',question))
    increased=bool(re.search(r'прибав\w*|увелич\w*|добав\w*|плюс|наценк\w*|больше|повыс\w*',question))
    if remaining and increased:
        return None
    result=Quantity(base.value-part.value if remaining else base.value+part.value,base.unit) if remaining or increased else part
    line=f'**{_shown(rate)}% от {_shown(base)} = {_shown(part)}.**'
    formula=f'{_shown(base)} × {_shown(rate)} ÷ 100 = {_shown(part)}'
    if remaining:
        line+=f'\n\nПосле вычета: **{_shown(result)}**.'
        formula+=f'\n{_shown(base)} − {_shown(part)} = {_shown(result)}'
    elif increased:
        line+=f'\n\nПосле прибавления: **{_shown(result)}**.'
        formula+=f'\n{_shown(base)} + {_shown(part)} = {_shown(result)}'
    rounding=any(_fmt(q.value,q.unit)[1] for q in [part,result])
    note=('\n\nРезультат округлён до двух знаков после запятой для суммы в валюте.' if base.unit else '\n\nРезультат округлён.') if rounding else ''
    return line+'\n\nРасчёт: '+formula+'.'+note


def _ratio(question):
    matches=list(_TOKEN.finditer(question))
    if len(matches)!=2 or any(m['percent'] for m in matches):return None
    if not re.search(r'сколько\s+процент|какой\s+процент|скольких\s+процент',question):return None
    signed=[]
    for match in matches:
        q=_quantity(match)
        signed.append(Quantity(-q.value,q.unit) if question[:match.start()].rstrip().endswith('-') else q)
    a,b=signed
    if a.unit and b.unit and a.unit!=b.unit:
        raise ArithmeticInputError('Укажите суммы в одной валюте или курс пересчёта.')
    changed=bool(re.search(r'вырос|рост|увелич|уменьш|сниз|измен',question))
    if not changed and re.search(r'(?:от|из)\s*$',question[:matches[0].start()]):a,b=b,a
    denominator=a if changed else b
    if denominator.value==0:raise ArithmeticInputError('Процент относительно нулевой базы не определён. Укажите ненулевую базу.')
    result=(b.value-a.value if changed else a.value)*100/denominator.value
    shown,rounded=_fmt(result)
    line=(f'Изменение с {_shown(a)} до {_shown(b)}: **{shown}%**.' if changed else
          f'{_shown(a)} от {_shown(b)} составляет **{shown}%**.')
    formula=(f'({_shown(b)} − {_shown(a)}) ÷ {_shown(a)} × 100' if changed else
             f'{_shown(a)} ÷ {_shown(b)} × 100')
    return line+f'\n\nРасчёт: {formula} = {shown}%.'+('\n\nРезультат округлён.' if rounded else '')


def _operation(operator,left,right):
    a,b=left.value,right.value
    if isinstance(operator,(ast.Add,ast.Sub)):
        if left.unit and right.unit and left.unit!=right.unit:
            raise ArithmeticInputError('Суммы в разных валютах нельзя складывать без курса. Укажите одну валюту или курс пересчёта.')
        result=Quantity(a+b if isinstance(operator,ast.Add) else a-b,left.unit or right.unit)
    elif isinstance(operator,ast.Mult):
        if left.unit and right.unit:
            raise ArithmeticInputError('Уточните единицы: умножение двух денежных сумм не даёт денежную сумму.')
        result=Quantity(a*b,left.unit or right.unit)
    elif isinstance(operator,ast.Div):
        if b==0:raise ArithmeticInputError('На ноль делить нельзя. Укажите ненулевой делитель.')
        if right.unit and left.unit!=right.unit:
            raise ArithmeticInputError('Уточните валюты и единицы перед делением.')
        result=Quantity(a/b,'' if right.unit else left.unit)
    else:raise ArithmeticInputError('Поддерживаются сложение, вычитание, умножение и деление.')
    if not result.value.is_finite() or abs(result.value)>Decimal('1e18'):
        raise ArithmeticInputError('Результат выходит за пределы этого калькулятора.')
    return result


def _expression(question):
    if re.search(r'\bv\d+\b',question):return None
    matches=list(_TOKEN.finditer(question))
    if not 1<=len(matches)<=24:return None
    chunks=[];last=0
    for i,m in enumerate(matches):
        chunks.extend([question[last:m.start()],f'v{i}']);last=m.end()
    chunks.append(question[last:]);expression=''.join(chunks).strip(' ?!=.')
    expression=_PREFIX.sub('',expression).strip()
    words=[(r'(?:сложи|сложить|сумма) (v\d+) (?:и|с) (v\d+)',r'\1 + \2'),
           (r'из (v\d+) (?:вычти|вычесть) (v\d+)',r'\1 - \2'),
           (r'(?:вычти|вычесть) (v\d+) из (v\d+)',r'\2 - \1'),
           (r'(?:умножь|умножить) (v\d+) на (v\d+)',r'\1 * \2'),
           (r'(?:раздели|разделить|подели|поделить) (v\d+) на (v\d+)',r'\1 / \2')]
    for pattern,replacement in words:
        if re.fullmatch(pattern,expression):
            expression=re.sub(pattern,replacement,expression);break
    for word,symbol in [('плюс','+'),('минус','-'),('умножить на','*'),('разделить на','/'),('делить на','/')]:
        expression=expression.replace(word,symbol)
    if len(expression)>256 or not re.fullmatch(r'(?:v\d+|[\s()+*/\-])+',expression):return None
    if not any(op in expression for op in '+-*/'):return None
    values={}
    for i,m in enumerate(matches):
        q=_quantity(m)
        values[f'v{i}']=Quantity(q.value/100) if m['percent'] else q
    tree=ast.parse(expression,mode='eval')
    if len(list(ast.walk(tree)))>96:raise ArithmeticInputError('Слишком длинное выражение. Разбейте расчёт на части.')
    def evaluate(node):
        if isinstance(node,ast.Name) and node.id in values:return values[node.id]
        if isinstance(node,ast.BinOp):return _operation(node.op,evaluate(node.left),evaluate(node.right))
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            q=evaluate(node.operand);return Quantity(-q.value if isinstance(node.op,ast.USub) else q.value,q.unit)
        raise ArithmeticInputError('Используйте числа, скобки и знаки +, −, ×, ÷.')
    result=evaluate(tree.body);display=expression
    for name,q in reversed(list(values.items())):display=re.sub(r'\b'+name+r'\b',_shown(q),display)
    display=display.replace('*','×').replace('/','÷')
    value,rounded=_fmt(result.value,result.unit)
    return f'**Результат: {value}.**\n\nРасчёт: {display} = {value}.'+('\n\nРезультат округлён.' if rounded else '')


def arithmetic_reply(question,previous_question=''):
    if not isinstance(question,str) or len(question)>1000:return None
    q=' '.join(question.casefold().replace('ё','е').replace('−','-').replace('×','*').replace('÷','/').split())
    if _NORMATIVE.search(q) or q.startswith(('!','расчет переплаты')):return None
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',q):return None
    try:
        with localcontext() as context:
            context.prec=50
            # A follow-up operation can use only explicit user arithmetic.
            # Never infer a tax rate, legal entitlement or a base from AI text.
            operation_text=q
            explicit_rate=None
            current_tokens=list(_TOKEN.finditer(q))
            if len(current_tokens)==1 and current_tokens[0]['percent']:
                token=current_tokens[0];prefix=q[:token.start()]
                # Strip only a demonstrative immediately before an explicit
                # percentage. Do not remove a sign or arbitrary user words.
                if not prefix.rstrip().endswith(('-', '+')):
                    prefix=re.sub(r'(?:этих|указанных|тех же)\s*$', '', prefix)
                    operation_text=' '.join((prefix+q[token.end():]).split())
                    explicit_rate=token.group()
            operation = re.fullmatch(r'(?:а )?(?:(?:сколько )?(?:останется|получится)|(?:после (?:вычета|удержания|прибавления|добавления))(?: сколько (?:останется|получится))?)(?: после (?:вычета|удержания|прибавления|добавления))?[?!. ]*', operation_text)
            if operation and re.search(r'вычет|удержан|прибавлен|добавлен', q):
                latest_rate = explicit_rate
                for previous in reversed(previous_question.splitlines()[-4:]):
                    previous = previous.casefold().replace('ё', 'е').strip()
                    if _NORMATIVE.search(previous): break
                    tokens = list(_TOKEN.finditer(previous))
                    rates = [m for m in tokens if m['percent']]
                    amounts = [m for m in tokens if not m['percent']]
                    bare = _PREFIX.sub('', previous).strip(' ?!.')
                    if len(rates) == 1 and not amounts and bare == rates[0].group().strip():
                        if latest_rate is None: latest_rate = rates[0].group()
                        continue
                    if (len(rates) == 1 and len(amounts) == 1 and _percent(previous)
                            and not re.search(r'остан|остат|прибав|добав|выч|удерж|увелич|уменьш|скидк|вся сумма|от какой|(?:^|\s)это\s+\d|составляет', previous)):
                        sign = 'прибавь' if re.search(r'прибавлен|добавлен', q) else 'вычти'
                        inherited = _percent(sign+' '+(latest_rate or rates[0].group())+' от '+amounts[0].group())
                        if inherited:
                            return inherited+'\n\nСумма и процент взяты из ваших предыдущих расчётных вопросов.'
                    break
                return 'Уточните исходную сумму и процент, который нужно вычесть или прибавить.'
            if '%' in q or re.search(r'процент(?:а|ов)?\b',q):
                answer=_expression(q) or _percent(q) or _ratio(q)
                if answer:return answer
                tokens=list(_TOKEN.finditer(q))
                if len(tokens)==1 and tokens[0]['percent']:
                    bare=_PREFIX.sub('',q).strip(' ?!.')
                    # A demonstrative names the same explicit user-supplied base;
                    # it adds no numeric input and never supplies a legal rate.
                    bare=re.sub(r'\s+от\s+(?:этой|той же|указанной)\s+суммы$', '', bare)
                    if bare==tokens[0].group().strip():
                        for previous in reversed(previous_question.splitlines()[-4:]):
                            previous=previous.casefold().replace('ё','е')
                            if re.search(r'остан|остат|прибав|добав|выч|удерж|увелич|уменьш|скидк|вся сумма|от какой|это\s+\d',previous):continue
                            if _NORMATIVE.search(previous) or not _percent(previous):continue
                            bases=[m for m in _TOKEN.finditer(previous) if not m['percent']]
                            if len(bases)==1:
                                inherited=_percent(tokens[0].group()+' от '+bases[0].group())
                                if inherited:return inherited+'\n\nБаза взята из вашего предыдущего расчёта.'
                        if previous_question:return None
                        return 'Укажите исходную сумму, от которой нужно посчитать процент. Например: «27% от 15 миллионов тенге».'
                return None
            return _expression(q)
    except ArithmeticInputError as exc:
        return '**Уточнение расчёта**\n\n'+str(exc)
    except (DecimalException,SyntaxError,RecursionError,ValueError):
        return None
