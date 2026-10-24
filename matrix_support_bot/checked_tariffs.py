"""Exact equal-column bank rows; decline uncertain products, periods and conditions."""
import re
from decimal import Decimal
from agents import render
from review_result import ReviewDetails
from offline_knowledge import is_tariff_question


def checked_tariff_answer(domain,question,evidence):
    current=question.split('\nПредыдущий вопрос:')[0]
    if (domain not in {'legal','finance'} or not is_tariff_question(current)
            or not re.search(r'halyk|халык',current,re.I)
            or not re.search(r'super\s*app|супер\s*апп',current,re.I)
            or not re.search(r'коммунал(?:ьн|к)',current,re.I)
            or re.search(r'kaspi|каспи|freedom|фридом|кредит|за[её]м|ипотек|вклад|депозит|взыск|юридическ|поставщик|сторонн|дополнител|штраф|пени|списал|почему|20\d{2}|\d{1,2}[./-]\d{1,2}|когда|дата|с какого|прошл|будущ|следующ|вчера|завтра',current,re.I)):
        return None
    pattern=re.compile(r'^\s*\d+[.)]?\s+Оплата коммунальных услуг\s+(\d+(?:[.,]\d+)?)\s*(?:тенге|теңге)\s+(\d+(?:[.,]\d+)?)\s*(?:тенге|теңге)\s*$',re.I)
    for p in evidence or []:
        if p['document_id']!='halyk-superapp' or p.get('bank')!='halyk':continue
        for row in p['text'].splitlines():
            match=pattern.fullmatch(row)
            if not match:continue
            first,second=(Decimal(v.replace(',','.')) for v in match.groups())
            if first!=second:return None  # Different client columns require a separate applicability check.
            amount=format(first.normalize(),'f').replace('.',',')
            return render(domain,'Комиссия за оплату коммунальных услуг в **Halyk SuperApp — '+amount+' тенге** по сохранённой строке тарифа. [S1]',question,[p],review=ReviewDetails((('S1',row.strip()),),False,'definition'))
    return None
