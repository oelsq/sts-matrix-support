"""Restrict explicit product questions to matching PDF evidence."""
import re
from response_guidance import salary_statistic_question, work_payment_question, statistic_period, statistic_territory

def scope_evidence(domain, question, evidence):
    q = question.casefold()
    # Bank and microcredit legislation have different subjects. Do not feed a
    # single-creditor question the other institution's special procedure.
    bank = bool(re.search(r'\bбанк(?:а|е|у|ом|и|ов|ах)?\b|банковск', q))
    mfo = bool(re.search(r'\bмфо\b|микрокредит|микрофинанс', q))
    if bank and not mfo:
        evidence = [p for p in evidence if p['document_id'] != 'microfinance']
    elif mfo and not bank:
        evidence = [p for p in evidence if p['document_id'] != 'banks']
    # Keep a wage non-payment answer focused on the payment rule. Other
    # articles mentioning wages do not establish a dispute procedure. Within
    # article 113, termination is a different case; don't feed its deadline to
    # an ongoing-employment question. Preserve original source/page identity.
    if work_payment_question(question) and any(
            p['document_id'] == 'labor' and str(p.get('section', '')).startswith('Статья 113.') for p in evidence):
        relevant = []
        for p in evidence:
            if p['document_id'] == 'labor-wage-guide' and re.search(r'куда|жалоб|жалова|инспекц|что дальше|не ответ', q):
                relevant.append(p)
                continue
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
        current=q.split('\nпредыдущий вопрос:')[0]
        metric = r"медиан\w*" if "медиан" in current or ('средн' not in current and 'медиан' in q) else r"средн\w*"
        year, quarter = statistic_period(question)
        annual=quarter is None and bool(re.search(r'за (?:весь )?20\d{2} год|за весь год|годов\w* показатель',current,re.I))
        territory = statistic_territory(question)
        evidence = [p for p in evidence
                    if p.get("document_kind") == "statistical_bulletin"
                    and (year is None or p.get('reporting_year') == year)
                    and (quarter is None or p.get('reporting_quarter') == quarter)
                    and (not annual or not p.get('reporting_quarter'))
                    and (territory is None or p.get('territory') == territory)
                    and re.search(metric + r"(?:\W+\w+){0,5}\W+(?:зарплат\w*|заработн\w*)", p["text"], re.I)
                    and re.search(r"\d[\d\s,.]*\s*(?:тенге|теңге)", p["text"], re.I)
                    and re.search(r"(?:квартал|месяц|полугод|год|20\d{2})", p["text"], re.I)]
    if domain == "finance" and re.search(r"halyk|халык", q) and re.search(r"super\s*app|супер\s*апп", q):
        evidence = [p for p in evidence if p["document_id"] == "halyk-superapp"]
        if "коммунальн" in q:
            evidence = [p for p in evidence if "оплата коммунальных услуг" in " ".join(p["text"].casefold().split())]
    return evidence
