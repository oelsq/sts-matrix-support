"""Restrict explicit product questions to matching PDF evidence."""
import re
from response_guidance import salary_statistic_question, work_payment_question

def scope_evidence(domain, question, evidence):
    q = question.casefold()
    # Keep a wage non-payment answer focused on the payment rule. Other
    # articles mentioning wages do not establish a dispute procedure. Within
    # article 113, termination is a different case; don't feed its deadline to
    # an ongoing-employment question. Preserve original source/page identity.
    if work_payment_question(question) and any(
            p['document_id'] == 'labor' and str(p.get('section', '')).startswith('Статья 113.') for p in evidence):
        relevant = []
        for p in evidence:
            if p['document_id'] != 'labor' or not str(p.get('section', '')).startswith('Статья 113.'):
                continue
            text = p['text']
            if not re.search(r'увол|прекращ|расторж', q):
                text = re.split(r'^\s*4\.\s', text, maxsplit=1, flags=re.M)[0].rstrip()
            if text.strip():
                relevant.append({**p, 'text': text})
        evidence = relevant
    # Salary statistics are not established by laws that merely mention wages.
    # Require the requested metric together with a value and reporting period.
    if salary_statistic_question(question):
        metric = r"медиан\w*" if "медиан" in q else r"средн\w*"
        evidence = [p for p in evidence
                    if p.get("document_kind") == "statistical_bulletin"
                    and re.search(metric + r"(?:\W+\w+){0,5}\W+(?:зарплат\w*|заработн\w*)", p["text"], re.I)
                    and re.search(r"\d[\d\s,.]*\s*(?:тенге|теңге)", p["text"], re.I)
                    and re.search(r"(?:квартал|месяц|полугод|год|20\d{2})", p["text"], re.I)]
    if domain == "finance" and re.search(r"halyk|халык", q) and re.search(r"super\s*app|супер\s*апп", q):
        evidence = [p for p in evidence if p["document_id"] == "halyk-superapp"]
        if "коммунальн" in q:
            evidence = [p for p in evidence if "оплата коммунальных услуг" in " ".join(p["text"].casefold().split())]
    return evidence
