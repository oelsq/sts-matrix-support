"""Shared pre-inference path used by the Matrix bot and answer evaluation."""
from dataclasses import dataclass
from term_definitions import checked_term_definition
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


def prepare_model_evidence(domain, question, knowledge, evidence=None):
    """One scoped source/exception path for production and forced inference tests."""
    if knowledge is None or domain == "general":
        return evidence
    if evidence is None:
        evidence = knowledge.search(domain, question, limit=8)
    def accessible(p):
        return (domain in p.get("domains", [p.get("domain")]) or
                (p.get("artifact_kind") == "official_text_snapshot" and
                 p.get("document_kind") in {"legislation", "supreme_court_resolution"} and not p.get("bank")))
    evidence = scope_evidence(domain, question, [p for p in evidence if accessible(p)])
    if evidence and getattr(knowledge, "qualifications", None):
        evidence = knowledge.qualifications.augment(evidence, question, limit=8)
    return scope_evidence(domain, question, [p for p in evidence if accessible(p)])[:8]


def prepare_local_reply(domain, question, knowledge, *, search_question=None, previous_question="", allow_model_without_sources=False):
    from bot_help import help_reply
    help_answer = help_reply(question, domain)
    if help_answer is not None:
        return PreparedReply(None, help_answer, "service_help")
    from cashflow_calculator import calculator_reply
    calculated=calculator_reply(question)
    if calculated:return PreparedReply(None,calculated,'cashflow_calculator')
    from arithmetic_reply import arithmetic_reply
    arithmetic = arithmetic_reply(question, previous_question=previous_question)
    if arithmetic is not None:
        return PreparedReply(None, arithmetic, "arithmetic")
    from task_preparation import preparation_reply
    preparation = preparation_reply(domain, question, previous_question=previous_question)
    if preparation is not None:
        return PreparedReply(None, preparation, "organizational_preparation")
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
    from document_drafts import credit_payment_problem
    from task_preparation import draft_task_text
    current_task = draft_task_text(question.split("\nПредыдущий вопрос:")[0])
    if (is_document_request(question) and credit_payment_problem(search_question)
            and not re.search(r'жалоб|исков|\bиск\b|\bсуд\w*|омбуд|прокурат|\bстать\w*|\bсрок\w*|обяз|по закону|законн|пен[яию]|штраф|неусто|рассчита|посчита', current_task, re.I)):
        return PreparedReply(None, practical_fallback(domain, search_question), "organizational_credit_draft")
    if knowledge is not None and domain != "general":
        evidence = scope_evidence(domain, search_question, knowledge.search(domain, search_question, limit=8))
        if not evidence:
            # Only the claim-audited workflow can generate source-free guidance.
            # Legacy/direct routes keep their existing conservative behaviour.
            if allow_model_without_sources:
                return PreparedReply([], None, "requires_guidance_model")
            return PreparedReply(evidence, practical_fallback(domain, search_question), "no_evidence_fallback")
        from checked_tariffs import checked_tariff_answer
        builders = [
            ('source_bound_tariff',lambda:checked_tariff_answer(domain,search_question,evidence)),
            ("source_bound_definition", lambda: checked_term_definition(domain, question, evidence)),
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
    if knowledge is None and domain in {'legal','finance'} and allow_model_without_sources:
        return PreparedReply([], None, "requires_guidance_model")
    evidence = prepare_model_evidence(domain, search_question, knowledge, evidence)
    return PreparedReply(evidence, None, "requires_model")
