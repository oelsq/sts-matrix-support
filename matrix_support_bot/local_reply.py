"""Shared pre-inference path used by the Matrix bot and answer evaluation."""
from dataclasses import dataclass
from agents import legal_aid_eligibility_answer
from checked_answers import checked_legal_answer, checked_work_payment_answer, checked_practical_guidance
from evidence_scope import scope_evidence
from checked_public_rights import checked_public_rights_answer
from checked_finance_terms import checked_gesv_answer
from response_guidance import practical_fallback, request_clarification


@dataclass
class PreparedReply:
    evidence: list | None
    answer: str | None
    mechanism: str


def prepare_local_reply(domain, question, knowledge, *, search_question=None, previous_question=""):
    from cashflow_calculator import calculator_reply
    calculated=calculator_reply(question)
    if calculated:return PreparedReply(None,calculated,'cashflow_calculator')
    search_question = search_question or question
    clarification = request_clarification(domain, search_question, previous_question)
    if clarification:
        return PreparedReply(None, clarification, "clarification")
    from case_facts import case_facts
    from response_guidance import is_document_request
    import re
    if (case_facts(question,previous_question)['user_reported_facts'].get('contract_reported')=='civil'
            and is_document_request(question) and re.search(r'не плат|не оплат|долг|задолж|оплат|требован',question,re.I)):
        from response_guidance import civil_payment_draft
        return PreparedReply(None,civil_payment_draft(),'organizational_civil_draft')
    evidence = None
    from document_drafts import is_rental_refund_draft
    if is_rental_refund_draft(search_question):
        return PreparedReply(None, practical_fallback(domain, search_question), "organizational_draft")
    if knowledge is not None and domain != "general":
        evidence = scope_evidence(domain, search_question, knowledge.search(domain, search_question, limit=8))
        if not evidence:
            return PreparedReply(evidence, practical_fallback(domain, search_question), "no_evidence_fallback")
        from checked_tariffs import checked_tariff_answer
        builders = [
            ('source_bound_tariff',lambda:checked_tariff_answer(domain,search_question,evidence)),
            ("source_bound_gesv", lambda: checked_gesv_answer(domain, search_question, evidence)),
            ("source_bound_public_rights", lambda: checked_public_rights_answer(domain, search_question, evidence)),
            ("source_bound_guidance", lambda: checked_practical_guidance(domain, search_question, evidence)),
            ("source_bound_work", lambda: checked_work_payment_answer(domain, search_question, evidence)),
            ("source_bound_legal", lambda: checked_legal_answer(search_question, evidence, domain=domain)),
        ]
        if domain == "legal":
            builders.append(("source_bound_legal_aid", lambda: legal_aid_eligibility_answer(question, evidence)))
        for mechanism, build in builders:
            answer = build()
            if answer and "Не удалось подготовить ответ с проверяемой" not in answer:
                return PreparedReply(evidence, answer, mechanism)
    if evidence and getattr(knowledge,"qualifications",None):
        evidence=knowledge.qualifications.augment(evidence,search_question,limit=8)
    return PreparedReply(evidence, None, "requires_model")
