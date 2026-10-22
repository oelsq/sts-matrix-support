"""Conservative numeric relation checks; not a substitute for semantic/legal review.

Recognized monetary, rate and duration claims must retain their metric and unit.
Unrecognized natural-language relationships still need the model reviewer.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).with_name('agents') / 'quality'
METRICS = json.loads((ROOT / 'numeric_metrics.json').read_text())
WORDS = json.loads((ROOT / 'number_words.json').read_text())
UNIT = re.compile(r"^\s*(%|процент[а-яё]*|[-–]?крат[а-яё]*|тенге|теңге|тг(?![а-яёa-z])|₸|kzt\b|доллар[а-яё]*|usd\b|\$|евро|eur\b|€|рубл[а-яё]*|rub\b|рабочи[а-яё]*\s+дн[а-яё]*|календарн[а-яё]*\s+дн[а-яё]*|дн[а-яё]*|день|месяц[а-яё]*|год[а-яё]*|лет(?![а-яёa-z]))",re.I)


def canonical_number(value):
    integer, _, fraction = value.replace(',', '.').partition('.')
    integer = integer.lstrip('0') or '0'
    fraction = fraction.rstrip('0')
    return integer + ('.' + fraction if fraction else '')


def normalize(text):
    text = re.sub(r'\[S\d+\]', '', text.casefold())
    text = re.sub(r'(?<!\d)\d{1,2}[.]\d{1,2}[.]\d{4}(?!\d)', ' DATE ', text)
    text = re.sub(r'(?:статья|статьи|пункт|подпункт|ст[.])\s+\d+(?:[-.]\d+)*', ' REFERENCE ', text)
    tens = '|'.join(k for k,v in WORDS.items() if v in range(20,100,10))
    ones = '|'.join(k for k,v in WORDS.items() if 1<=v<=9 and not k.endswith(('варя','раля','арта','реля','мая','юня','юля','густа','ября')))
    text = re.sub(r'(?<![а-яё])('+tens+r')\s+('+ones+r')(?![а-яё])',lambda m:str(WORDS[m[1]]+WORDS[m[2]]),text)
    text = re.sub(r'[а-яё]+',lambda m:str(WORDS[m[0]]) if m[0] in WORDS and m[0] not in {'января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'} else m[0],text)
    return re.sub(r'(?<=\d)[ \u00a0\u202f](?=\d{3}(?:\D|$))','',text)


def unit_name(text):
    if text.startswith(('%','процент')):return 'percent'
    if 'крат' in text:return 'multiplier'
    if re.match(r'тенге|теңге|тг|₸|kzt',text):return 'KZT'
    if re.match(r'доллар|usd|\$',text):return 'USD'
    if re.match(r'евро|eur|€',text):return 'EUR'
    if re.match(r'рубл|rub',text):return 'RUB'
    if text.startswith('рабоч'):return 'workday'
    if text.startswith('календар'):return 'calendar_day'
    if text.startswith(('дн','день')):return 'day'
    if text.startswith('месяц'):return 'month'
    return 'year'


def numeric_facts(text):
    facts=[]
    # Keep decimal commas/points; independent clauses cannot borrow a metric.
    clauses=re.split(r'[;\n]|(?<!\d)[.!?]|[.!?](?!\d)|,(?!\d)',normalize(text))
    for clause in clauses:
        labels=[]
        for metric,pattern in METRICS:
            labels.extend((m.start(),m.end(),metric) for m in re.finditer(pattern,clause,re.I))
        for number in re.finditer(r'(?<![a-zа-яё0-9_])\d+(?:[.,]\d+)?',clause):
            unit=UNIT.match(clause[number.end():])
            if not unit:continue
            before=[x for x in labels if x[1]<=number.start() and number.start()-x[1]<=100]
            after=[x for x in labels if x[0]>=number.end() and x[0]-number.end()<=65]
            label=max(before,key=lambda x:x[1]) if before else (min(after,key=lambda x:x[0]) if after else None)
            if not label:continue
            prefix=clause[max(0,number.start()-35):number.start()]
            bound='max' if re.search(r'не более|не превыш[а-яё]*|до\s*$|максимальн|предельн',prefix) else ('min' if re.search(r'не менее|от\s*$|минимальн',prefix) else 'exact')
            facts.append((label[2],canonical_number(number[0]),unit_name(unit[1]),bound))
    return facts


def numeric_relation_errors(answer, proof_texts):
    supported=set(fact for text in proof_texts for fact in numeric_facts(text))
    errors=[]
    for metric,value,unit,bound in numeric_facts(answer):
        exact=(metric,value,unit,bound) in supported or (bound!='exact' and (metric,value,unit,'exact') in supported)
        recognizable=any(m==metric or v==value for m,v,u,b in supported)
        if recognizable and not exact:
            errors.append('unproven_numeric_relation:'+metric+':'+value+':'+unit+':'+bound)
    return errors
