"""Bounded usefulness checks; never a semantic/legal correctness certificate."""
import re
from response_guidance import is_document_request

HELP_FIRST_INSTRUCTIONS = (
    "Сначала решай задачу пользователя своими средствами. Для практической проблемы дай конкретное первое действие "
    "и объясни, зачем оно нужно; для справки — прямой подтверждённый ответ. "
    "Если известна только часть ответа, объясни эту часть, предложи безопасный следующий шаг и уточни только недостающую деталь. "
    "Не подменяй помощь фразами «обратитесь в департамент», «это к юристу» или «самостоятельно изучите закон». "
    "Не скрывай реальные ограничения и не выдумывай сроки/нормы ради уверенного ответа. "
    "Если просят письмо, дай сам текст с заполнителями, а не указание «напишите письмо». "
    "Не добавляй стандартную рекомендацию департамента в каждый ответ. "
    "Переход к сотруднику — по желанию пользователя либо при конкретном серьёзном риске; "
    "даже тогда сначала дай доступное действие, которое нельзя откладывать."
)

HUMAN_REQUEST = re.compile(r"(?:хочу|нужен|нужна|позови|соедини|переключи|передай).{0,45}(?:человек|сотрудник|департамент|юрдеп|финдеп)|как (?:связаться|обратиться).{0,35}(?:департамент|сотрудник)", re.I)
SERIOUS_RISK = re.compile(r"(?:меня|его|е[её]|нас)\s+(?:задержал|задержив|арестовал)|(?:сегодня|завтра).{0,45}(?:судебн.*заседан|суд\b|истекает.*срок)|(?:суд\b|крайний срок).{0,35}(?:сегодня|завтра)|угрож\w*.{0,45}(?:уби|расправ|насили|жизн)", re.I)


def answer_body(answer):
    body = re.split(r"\n(?:\n)?(?:Локальные материалы для проверки|Материалы для самостоятельной проверки|(?:\*\*)?Источники)", answer, maxsplit=1)[0]
    body = re.sub(r"^\s*(?:\*\*)?(?:Юридический агент|Финансовый агент)(?:\*\*)?\s*", "", body)
    return body.strip()


def usefulness_issues(question, answer):
    """Catch obvious deflections/missing artefacts; a human must judge meaning."""
    body = answer_body(answer)
    if HUMAN_REQUEST.search(question):
        return []
    issues = []
    if re.match(r"(?:в (?:предоставленных|доступных)(?: локальных)? (?:документах|фрагментах) нет норм|не могу (?:дать|предоставить) ответ)", body, re.I):
        issues.append("premature_refusal")
    if re.match(r"(?:сначала\s+|вам\s+(?:нужно|необходимо)\s+)?(?:обратитесь|проконсультируйтесь|напишите\s+!человек)", body, re.I) and re.search(r"департамент|финдеп|юрдеп|юрист|сотрудник|специалист|!человек", body.split("\n\n")[0], re.I) and not SERIOUS_RISK.search(question):
        issues.append("premature_department_referral")
    sentences = re.split(r"(?<=[.!?])\s+|\n+", body)
    remaining = []
    delegated = False
    for sentence in sentences:
        if re.search(r"обратит\w*|передат\w*|направит\w*|проконсульт\w*|провер\w*.*сотрудник|!человек|!юрдеп|!финдеп", sentence, re.I) and re.search(r"департамент|финдеп|юрдеп|специалист|юрист|сотрудник|!человек", sentence, re.I):
            delegated = True
            continue
        if re.search(r"не могу|не хватает|нет (?:данных|норм|информации)|не располагаю|не могу гарантировать", sentence, re.I):
            continue
        remaining.append(sentence)
    substantive = re.sub(r"\[(?:S\d+|[^\[\] ]*:[^\[\] ]*)\]|[\W\d_]+", " ", " ".join(remaining))
    if delegated and len(substantive.split()) < 8:
        issues.append("handoff_only")
    if is_document_request(question):
        # This does not establish the legal validity/completeness of a letter.
        # It only rejects a checklist offered instead of the requested text.
        draft = (re.search(r"(?:Кому\s*:|(?:Арендодателю|Работодателю|Продавцу|Банку)\s*:|От(?:\s+[^:\n]{1,35})?\s*:|В\s+\[|Уважаем\w*|Здравствуйте)", body, re.I)
                 and re.search(r"(?:Прошу|Требую|Сообщаю|Обращаюсь)", body, re.I)
                 and len(body.split()) >= 30)
        if not draft:
            issues.append("requested_draft_missing")
    return issues


def should_offer_department(question, answer):
    """A normal helpful answer must not carry an automatic department footer."""
    current = question.split("\nПредыдущий вопрос:")[0]
    if HUMAN_REQUEST.search(current) or SERIOUS_RISK.search(current):
        return True
    return bool(re.search(r"(?:ответ|совет|помощь).{0,25}не помог|не помог(?:ло|ает)|не устраивает.*ответ|хочу (?:другую|вторую) оценку", current, re.I))


def department_note(question, answer):
    if not should_offer_department(question, answer):
        return ""
    if re.search(r"!человек|!юрдеп|!финдеп", answer):
        return ""
    return "\n\nЕсли нужна проверка сотрудника по этой ситуации, напишите !человек."
