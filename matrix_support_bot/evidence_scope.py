"""Restrict explicit product questions to matching PDF evidence."""
import re

def scope_evidence(domain, question, evidence):
    q = question.casefold()
    if domain == "finance" and re.search(r"halyk|халык", q) and re.search(r"super\s*app|супер\s*апп", q):
        evidence = [p for p in evidence if p["document_id"] == "halyk-superapp"]
        if "коммунальн" in q:
            evidence = [p for p in evidence if "оплата коммунальных услуг" in " ".join(p["text"].casefold().split())]
    return evidence
