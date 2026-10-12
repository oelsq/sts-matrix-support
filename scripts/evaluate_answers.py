#!/usr/bin/env python3
"""Repeatable corpus checks and optional real model/Flowise checks. Sends no Matrix messages."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "matrix_support_bot"))
from offline_knowledge import KnowledgeStore
from evidence_scope import scope_evidence


def case_context(case):
    from conversation import ConversationMemory
    memory = ConversationMemory()
    for question in case.get("previous_questions", []):
        memory.resolve("synthetic-room", "synthetic-user", case["domain"], question)
    return memory.resolve("synthetic-room", "synthetic-user", case["domain"], case["question"])


def evaluate_case(store, case):
    start = time.monotonic()
    search_question, _ = case_context(case)
    evidence = scope_evidence(case["domain"], search_question, store.search(case["domain"], search_question, 8))
    if case.get("expect_empty"):
        passed = not evidence
    else:
        passed = any(p["document_id"] == case["document"] and
                     (not case.get("section") or str(p.get("section", "")).startswith(case["section"]))
                     for p in evidence[:case.get("top_k", 5)])
    return {"id": case["id"], "passed": passed, "elapsed_ms": round((time.monotonic()-start)*1000, 1),
            "sources": [{"id": p["id"], "document": p["document_id"], "section": p.get("section", "")} for p in evidence]}


async def live_checks(store, cases, route):
    import aiohttp
    from agents import messages, review_messages, render
    from checked_answers import checked_work_payment_answer
    from flowise_client import FlowiseClient
    from llm_settings import api_key, model_for_domain
    flow = FlowiseClient.from_env(os.environ) if route in {"flowise", "both"} else None
    if route in {"flowise", "both"} and not flow:
        raise RuntimeError("Flowise is not configured; checks were not run")
    # Only synthetic public questions; no user conversation, account or secrets in output.
    results = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as http:
        for transport in (["direct", "flowise"] if route == "both" else [route]):
            for case in cases:
                if case.get("expect_empty"):
                    continue
                domain, question = case["domain"], case["question"]
                search_question, previous_question = case_context(case)
                evidence = scope_evidence(domain, search_question, store.search(domain, search_question, 8))
                start = time.monotonic()
                async def complete(msgs):
                    if transport == "flowise":
                        room = next(r for r, entry in flow.routes.items() if entry["domain"] == domain)
                        return await flow.predict(http, domain, room, "synthetic-evaluation", case["id"], msgs)
                    async with http.post(os.environ["LLM_ENDPOINT"].rstrip("/") + "/chat/completions",
                                         headers={"Authorization": "Bearer " + api_key()},
                                         json={"model": model_for_domain(domain), "messages": msgs, "max_tokens": 2300, "temperature": 0.1},
                                         allow_redirects=False) as response:
                        response.raise_for_status()
                        data = await response.json()
                    if data["choices"][0].get("finish_reason") == "length":
                        raise RuntimeError("Model response truncated")
                    return data["choices"][0]["message"]["content"]
                try:
                    if not evidence:
                        raise ValueError("No scoped evidence")
                    answer = checked_work_payment_answer(domain, search_question, evidence)
                    generation = "checked_source_template" if answer else "model"
                    if answer is None:
                        draft = await complete(messages(domain, question, evidence, previous_question))
                        reviewed = await complete(review_messages(domain, search_question, draft, evidence))
                        answer = render(domain, reviewed, question, evidence)
                    passed = "Не удалось подготовить ответ с проверяемой" not in answer
                    required = case.get("answer_contains", [])
                    passed = passed and all(term.casefold() in answer.casefold() for term in required)
                    passed = passed and not any(term.casefold() in answer.casefold() for term in case.get("answer_forbids", []))
                    result = {"id": case["id"], "route": transport, "passed": passed, "answer": answer,
                              "sources": [{"document": p["document_id"], "section": p.get("section", "")} for p in evidence],
                              "continued": bool(previous_question), "generation": generation}
                except Exception as exc:
                    result = {"id": case["id"], "route": transport, "passed": False, "error_type": type(exc).__name__}
                result["elapsed_ms"] = round((time.monotonic()-start)*1000, 1)
                results.append(result)
                print(json.dumps({k:v for k,v in result.items() if k != "answer"}, ensure_ascii=False), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", default=str(ROOT / "offline_knowledge"))
    parser.add_argument("--cases", default=str(ROOT / "config/answer-evaluation.json"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--live", choices=["direct", "flowise", "both"])
    parser.add_argument("--only", help="Comma-separated case IDs for bounded live checks")
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text())["cases"]
    if args.only:
        names = set(args.only.split(","))
        if names - {c["id"] for c in cases}:
            parser.error("Unknown case ID")
        cases = [c for c in cases if c["id"] in names]
    if args.live and not any(not c.get("expect_empty") for c in cases):
        parser.error("Live evaluation needs at least one question with expected evidence")
    store = KnowledgeStore(args.knowledge)
    retrieval = [evaluate_case(store, c) for c in cases]
    live = asyncio.run(live_checks(store, cases, args.live)) if args.live else []
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "passages": len(store.passages),
              "retrieval": retrieval, "live": live, "matrix_messages_sent": False,
              "limits": "Retrieval and automated citation checks do not establish semantic or legal correctness. Review live answers manually."}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    failed = sum(not row["passed"] for row in retrieval + live)
    print(f"Retrieval: {sum(r['passed'] for r in retrieval)}/{len(retrieval)}; live: {sum(r['passed'] for r in live)}/{len(live)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
