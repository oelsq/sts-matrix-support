"""Repeat exact, scoped source slices near a task; never invent a legal summary."""
import re

PATTERNS = {
    'consumer-recording-law-exception':[r'Запрещается продавцу.{0,400}видеосъемки'],
    'marriage-child-property-vs-parent-duties':[
        r'имущественное положение детей', r'права и обязанности супругов в отношении детей'],
    'child-opinion-not-sole-residence-decision':[
        r'учет мнения ребенка', r'место жительства.{0,160}соглашением родителей',
        r'при отсутствии соглашения спор между родителями'],
}


def focused_sources(sources, constraints, maximum=6):
    result=[];seen=set()
    for constraint in constraints:
        for pattern in PATTERNS.get(constraint['id'],[]):
            found=False
            for source in sources:
                for excerpt in source.get('excerpts',[]):
                    if re.search(pattern, ' '.join(excerpt['quote'].split()), re.I):
                        key=(source['id'],excerpt['id'])
                        if key not in seen:
                            seen.add(key);result.append({'source_id':source['id'],'quote_id':excerpt['id'],'quote':excerpt['quote']})
                        found=True;break
                if found:break
            if len(result)>=maximum:return result
    return result
