"""Explicit date bounds and tariff review age; no inferred legal currency."""
from datetime import date
import re


def applicable(source,as_of=None):
    today=as_of or date.today()
    if re.search(r'утратил|утратив|отмен[её]н',str(source.get('status','')),re.I):return False
    for key,op in [('valid_from',lambda d:today>=d),('valid_until',lambda d:today<=d)]:
        if source.get(key):
            try:
                if not op(date.fromisoformat(source[key])):return False
            except (ValueError,TypeError):return False
    return True


def tariff_review_due(sources,as_of=None):
    today=as_of or date.today();ids=[]
    for p in sources or []:
        if not p.get('bank'):continue
        try:age=(today-date.fromisoformat(p['reviewed_on'])).days
        except (KeyError,ValueError,TypeError):age=None
        if age is None or age<0 or age>7:ids.append(p.get('document_id',''))
    return sorted(set(ids))


def tariff_note(question,sources):
    from offline_knowledge import is_tariff_question,is_rights_question
    if is_tariff_question(question) and not is_rights_question(question) and tariff_review_due(sources):
        return 'Тариф приведён по сохранённому документу. Нужна повторная проверка: текущая комиссия может отличаться.'
    return ''
