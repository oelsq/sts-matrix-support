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
    search_question = search_question or question
    clarification = request_clarification(domain, search_question, previous_question)
    if clarification:
        return PreparedReply(None, clarification, "clarification")
    evidence = None
    from document_drafts import is_rental_refund_draft
    if is_rental_refund_draft(search_question):
        return PreparedReply(None, practical_fallback(domain, search_question), "organizational_draft")
    if knowledge is not None and domain != "general":
        evidence = scope_evidence(domain, search_question, knowledge.search(domain, search_question, limit=8))
        if not evidence:
            return PreparedReply(evidence, practical_fallback(domain, search_question), "no_evidence_fallback")
        builders = [
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
    return PreparedReply(evidence, None, "requires_model")
