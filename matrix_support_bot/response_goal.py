"""Task coverage, separate from citation validity; never an expert verdict."""
import re

def response_goal(question, previous='', *, format_type='situation'):
    current=question.split('\nПредыдущий вопрос:')[0]
    if format_type=='document':
        kind='document'; instruction='Дай сам пригодный для заполнения текст документа, сохрани факты и вид отношений из предыдущего вопроса.'
    elif format_type=='calculation':
        kind='calculation'; instruction='Дай расчёт с методом и исходными данными либо укажи, каких именно данных не хватает. Не подставляй неизвестную ставку или срок.'
    elif re.search(r'можно ли|могу ли|может ли|могут ли|можем ли|должен ли|должна ли|должны ли|разрешено ли|нужно ли|требуется ли|законно ли|правомер|\b(?:обязан(?:а|ы)?|вправе)\b.{0,70}\?\s*$|име[юе]т? ли.{0,20}право|какие.{0,25}права|(?:уже|автоматич|сразу).{0,60}собственност|кто.{0,20}собственник|кому.{0,30}принадлеж',current,re.I):
        kind='permission'; instruction='Сначала объясни, что допускается или ограничено, при каких условиях и почему. Одних советов собрать документы недостаточно.'
    elif re.search(r'отлич|разниц|(?:^|[.!?]\s*)(?:помоги\s+)?сравн|то же самое|тот же случай|одно и то же|одно (?:разрешение|правило)|одинаков[а-яё]* ли',current,re.I):
        kind='comparison'; instruction='Сопоставь оба понятия по существу и назови практическое различие и существенное исключение.'
    elif format_type=='definition':
        kind='definition'; instruction='Сначала объясни смысл понятия простыми словами и его существенную границу; не заменяй определение планом действий.'
    else:
        kind='situation'; instruction='Объясни применимый к ситуации вывод и конкретный следующий шаг. Не требуй уже сообщённые сведения повторно.'
    if previous:
        instruction+=' Короткое уточнение относится к предыдущему вопросу, если пользователь явно не сменил тему.'
    return {'version':1,'kind':kind,'instruction':instruction,
            'requires_explanation':kind in {'permission','definition','comparison'}}

def coverage_errors(audit, expected, *, require=False, explanation=False):
    """Check selected claim IDs, not their legal interpretation (model's job)."""
    value=audit.get('answer_unit_ids') if isinstance(audit,dict) else None
    if not require:return []
    if not isinstance(value,str) or len(value)>160:return ['invalid_answer_coverage']
    ids=value.split(',') if value else []
    if not ids or any(not re.fullmatch(r'C(?:[1-9]|[12][0-9]|3[0-2])',i.strip()) for i in ids):return ['main_question_unanswered']
    ids=[i.strip() for i in ids]
    if len(set(ids))!=len(ids) or not set(ids)<={u['id'] for u in expected}:return ['invalid_answer_coverage']
    if explanation and not any(r.get('unit_id') in ids and r.get('status')=='supported' for r in audit.get('checks',[])):
        return ['main_question_unanswered']
    return []
