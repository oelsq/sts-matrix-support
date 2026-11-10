#!/usr/bin/env python3
"""Repeatable corpus checks and optional real model checks. Sends no Matrix messages."""
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
    from evaluate_helpfulness import live_answer
    from answer_usefulness import usefulness_issues
    if route != 'direct':raise ValueError('Only direct model evaluation is supported')
    results=[]
    for case in cases:
        if case.get('expect_empty'):continue
        started=time.monotonic()
        search,previous=case_context(case)
        evidence=scope_evidence(case['domain'],search,store.search(case['domain'],search,8))
        try:
            answer=await live_answer(case['domain'],case['question'],evidence,previous,'direct',case['id'],knowledge=store)
            issues=usefulness_issues(case['question'],answer)
            passed=not issues and 'Не удалось получить ответ модели' not in answer
            results.append({'id':case['id'],'route':'direct','passed':passed,'generation':'direct_model',
                'answer':answer,'elapsed_ms':round((time.monotonic()-started)*1000,1),'issues':issues})
        except Exception as exc:
            results.append({'id':case['id'],'route':'direct','passed':False,'error':type(exc).__name__})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", default=str(ROOT / "offline_knowledge"))
    parser.add_argument("--cases", default=str(ROOT / "config/answer-evaluation.json"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--live", choices=["direct"])
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
