"""Exact cash-flow arithmetic. No APR, legal interest or assumed loan terms."""
from datetime import date
from decimal import Decimal, InvalidOperation
import json,re

TEMPLATE='''**Для расчёта пришлите полный график в таком виде:**

```text
Расчёт переплаты
Получено на руки: 100000 KZT
Дата получения: 2026-10-01
Платежи:
2026-11-01 | 55000
2026-12-01 | 55000
Дополнительные расходы вне платежей: 0
График полный: да
```

Замените пример своими данными. В дополнительных расходах укажите только платежи, не включённые в график и не удержанные до выдачи денег. Имена, номера договоров и счетов не нужны.'''

class CalculationInputError(ValueError):pass


def money(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,Decimal)):raise CalculationInputError('Сумма должна быть точным числом без округления.')
    text=str(value).replace('\u00a0',' ').replace('\u202f',' ').strip()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?: \d{3})+)(?:[.,]\d{1,2})?',text):raise CalculationInputError('Используйте суммы с точностью до двух знаков после запятой.')
    try:n=Decimal(text.replace(' ','').replace(',','.'))
    except InvalidOperation:raise CalculationInputError('Не удалось прочитать сумму.') from None
    if not n.is_finite() or n<0 or n>Decimal('1000000000000'):raise CalculationInputError('Сумма вне допустимого диапазона.')
    return n


def parse_table(text):
    data={'payments':[]};in_payments=False;seen=set()
    for line in text.splitlines()[1:]:
        line=line.strip()
        if not line or line.startswith('```'):continue
        if line=='Платежи:':in_payments=True;continue
        if in_payments and re.fullmatch(r'\d{4}-\d{2}-\d{2}\s*\|\s*[\d ,.\u00a0\u202f]+',line):
            day,amount=line.split('|');data['payments'].append({'date':day.strip(),'amount':amount.strip()});continue
        in_payments=False
        label=line.split(':',1)[0]
        if label in seen:raise CalculationInputError('Поле повторяется: оставьте одно значение каждого параметра.')
        seen.add(label)
        if line.startswith('Получено на руки:'):
            match=re.fullmatch(r'Получено на руки:\s*(.+?)\s+(KZT|USD|EUR)',line,re.I)
            if not match:raise CalculationInputError('Укажите полученную на руки сумму и одну валюту: KZT, USD или EUR.')
            data['amount_received'],data['currency']=match[1],match[2].upper()
        elif line.startswith('Дата получения:'):data['received_on']=line.split(':',1)[1].strip()
        elif line.startswith('Дополнительные расходы вне платежей:'):data['extra_costs']=line.split(':',1)[1].strip()
        elif line.startswith('График полный:'):data['schedule_complete']=line.split(':',1)[1].strip().casefold()=='да'
        else:raise CalculationInputError('Есть нераспознанная строка; используйте формат примера без дополнительных полей.')
    return data


def calculate(data):
    if not isinstance(data,dict) or set(data)!={'amount_received','received_on','currency','payments','extra_costs','schedule_complete'}:raise CalculationInputError('Нужны полученная сумма, валюта, дата, полный график и отдельные расходы, включая нулевые.')
    if data['schedule_complete'] is not True:raise CalculationInputError('Для итогового расчёта нужен полный график; по части платежей итоговую переплату определить нельзя.')
    if data['currency'] not in {'KZT','USD','EUR'}:raise CalculationInputError('Для расчёта нужна одна валюта: KZT, USD или EUR.')
    try:received=date.fromisoformat(data['received_on'])
    except (TypeError,ValueError):raise CalculationInputError('Дата получения должна иметь вид ГГГГ-ММ-ДД.') from None
    amount=money(data['amount_received']);extra=money(data['extra_costs'])
    if amount<=0:raise CalculationInputError('Полученная сумма должна быть больше нуля.')
    rows=data['payments']
    if not isinstance(rows,list) or not 1<=len(rows)<=1200:raise CalculationInputError('Нужен график от 1 до 1200 платежей.')
    total=Decimal('0');last=received
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'date','amount'}:raise CalculationInputError('Каждый платёж должен содержать только дату и сумму в общей валюте.')
        try:day=date.fromisoformat(row['date'])
        except (TypeError,ValueError):raise CalculationInputError('Проверьте даты платежей.') from None
        if day<received or day<last:raise CalculationInputError('Платежи нужно перечислить по датам, начиная с получения денег.')
        last=day;total+=money(row['amount'])
    over=total+extra-amount
    if over<0:raise CalculationInputError('Выплаты меньше полученной суммы: проверьте полноту графика или отдельно объясните списание/субсидию.')
    return {'currency':data['currency'],'amount_received':str(amount),'scheduled_payments':str(total),'extra_costs':str(extra),'total_outflow':str(total+extra),'cashflow_difference':str(over),'payment_count':len(rows),'received_on':received.isoformat(),'last_payment_on':last.isoformat()}


def calculator_reply(question):
    q=re.sub(r'^```(?:text)?\s*\n|\n```$', '',question.strip()).strip()
    if not re.match(r'^(?:!расч[её]т\b|расч[её]т переплаты(?:\s|$))',q,re.I):return None
    if len(q)>80000:return '**Расчёт переплаты**\n\nГрафик слишком большой; разбейте его на таблицу до 1200 строк.'
    try:
        payload=q.split('\n',1)[1] if '\n' in q else ''
        data=json.loads(payload) if payload.lstrip().startswith('{') else parse_table(q)
        result=calculate(data)
    except (CalculationInputError,ValueError,TypeError) as exc:
        message=str(exc) if isinstance(exc,CalculationInputError) else 'Не удалось прочитать график.'
        return '**Расчёт переплаты**\n\n'+message+'\n\n'+TEMPLATE
    def fmt(key):return f"{Decimal(result[key]):,.2f}".replace(',',' ').replace('.',',')+' '+result['currency']
    return ('**Расчёт по вашему графику**\n\n'
      f"- Получено на руки: **{fmt('amount_received')}**.\n"
      f"- Сумма {result['payment_count']} платежей: **{fmt('scheduled_payments')}**.\n"
      f"- Дополнительные расходы вне графика: **{fmt('extra_costs')}**.\n"
      f"- Всего последующих выплат: **{fmt('total_outflow')}**.\n\n"
      f"Превышение выплат над полученной суммой: **{fmt('cashflow_difference')}**.\n\n"
      'Метод: платежи по графику + отдельные расходы − фактически полученная сумма. '
      'Удержанные до выдачи расходы уже учтены через полученную на руки сумму; повторно их не прибавляйте. '
      'Результат основан на переданных вами данных. ГЭСВ, законность комиссий и будущие штрафы этим расчётом не определяются.')
