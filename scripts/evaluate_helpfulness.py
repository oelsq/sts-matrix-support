#!/usr/bin/env python3
"""Assess actual reply text separately from retrieval; no inferred model success."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'matrix_support_bot'))
from answer_usefulness import answer_body, usefulness_issues, department_note
from conversation import ConversationMemory
from local_reply import prepare_local_reply
from message_format import matrix_content
from offline_knowledge import KnowledgeStore
from urgent_help import urgent_finance_answer


def assess(case, answer):
    issues = usefulness_issues(case['question'], answer)
    body = answer_body(answer)
    for index, pattern in enumerate(case.get('required_patterns', []), 1):
        if not re.search(pattern, body, re.I):
            issues.append('missing_requested_content_' + str(index))
    if 'Не удалось подготовить ответ с проверяемой' in answer:
        issues.append('failed_citation_validation')
    return {'automated_issues': issues, 'automated_checks_passed': not issues,
            'body_words': len(body.split()),
            'department_offer': bool(re.search(r'!человек|!юрдеп|!финдеп', answer)),
            'semantic_correctness_verified': False}


def archive_review():
    """Historical saved model outputs, not fresh model executions."""
    records = []
    source = 'reports/quality-wide-20261013.json'
    data = json.loads((ROOT / source).read_text())
    for phase in ('baseline', 'candidate', 'refinement', 'real_matrix'):
        for index, row in enumerate(data.get(phase, [])):
            if row.get('generation') == 'agentflow_v2' and row.get('answer'):
                records.append((source, f'/{phase}/{index}', row))
    source = 'reports/model-comparison-20261012.json'
    for index, row in enumerate(json.loads((ROOT/source).read_text()).get('cases', [])):
        if row.get('answer'):
            records.append((source, f'/cases/{index}', row))
    unique, rows = set(), []
    for file, path, row in records:
        signature = hashlib.sha256((row.get('question', '') + '\0' + row['answer']).encode()).hexdigest()
        if signature in unique:
            continue
        unique.add(signature)
        rows.append({'source_file': file, 'json_pointer': path, 'domain': row.get('domain'),
                     'question': row.get('question'), 'model': row.get('model'),
                     'generation': row.get('generation', 'direct_model_comparison'),
                     'answer_sha256': signature, 'historical': True,
                     **assess(row, row['answer'])})
    return {'records_seen': len(records), 'unique_saved_model_answers': len(rows),
            'flagged': sum(bool(row['automated_issues']) for row in rows), 'rows': rows,
            'limits': 'Past model answers from October 12–13, including older baselines. Not a current run or a new model quality score.'}


async def live_answer(domain, question, evidence, previous, mode, case_id):
    import os
    import aiohttp
    from agents import messages, review_messages, render
    from flowise_client import FlowiseClient
    from llm_settings import api_key, model_for_domain
    flow = FlowiseClient.from_env(os.environ) if mode == 'flowise' else None
    if mode == 'flowise' and not flow:
        raise RuntimeError('Flowise configuration is unavailable')
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=100)) as http:
        request = messages(domain, question, evidence, previous)
        review_question = question + ("\nПредыдущий вопрос: " + previous if previous else "")
        for attempt in range(2):
            if flow:
                from agentflow_graphs import MARKER
                room = next(room for room, entry in flow.routes.items() if entry['domain'] == domain)
                if not flow.is_workflow(room, domain):
                    raise RuntimeError('Expected Agentflows V2 route')
                text = await flow.run_workflow(http, domain, room, 'synthetic-helpfulness-review', case_id,
                                               request, review_messages(domain, review_question, MARKER, evidence))
            else:
                async def complete(msgs):
                    async with http.post(os.environ['LLM_ENDPOINT'].rstrip('/')+'/chat/completions',
                        headers={'Authorization':'Bearer '+api_key()},
                        json={'model':model_for_domain(domain),'messages':msgs,'temperature':0.1,'max_tokens':2300},
                        allow_redirects=False) as response:
                        response.raise_for_status();data=await response.json()
                    if data['choices'][0].get('finish_reason')=='length':
                        raise RuntimeError('Truncated model response')
                    return data['choices'][0]['message']['content']
                draft = await complete(request)
                text = await complete(review_messages(domain, review_question, draft, evidence))
            answer = render(domain, text, question, evidence)
            issues = usefulness_issues(question, answer)
            if 'Не удалось подготовить ответ с проверяемой' not in answer and not issues:
                return answer
            request += [{'role':'assistant','content':text}, {'role':'user','content':
                'Исправь проверку подтверждений и ответь по существу. Не отправляй к сотруднику вместо ответа. Если просили письмо, дай сам текст. '+', '.join(issues)}]
        return answer


async def evaluate(cases, knowledge, live):
    results = []
    for case in cases:
        started = time.monotonic()
        memory = ConversationMemory()
        for previous in case.get('previous_questions', []):
            memory.resolve('synthetic-room', 'synthetic-user', case['domain'], previous)
        search, previous = memory.resolve('synthetic-room', 'synthetic-user', case['domain'], case['question'])
        row = {'id':case['id'],'domain':case['domain'],'question':case['question'],
               'previous_questions':case.get('previous_questions', [])}
        urgent = urgent_finance_answer(case['question'], knowledge)
        prepared = prepare_local_reply(case['domain'], case['question'], knowledge,
                                       search_question=search, previous_question=previous)
        answer = urgent or prepared.answer
        mechanism = 'source_bound_urgent' if urgent else prepared.mechanism
        row['mechanism'] = mechanism
        row['real_model_executed'] = False
        row['real_model_attempted'] = False
        if answer is None:
            if not live:
                row.update(status='requires_live_model', answer=None, automated_checks_passed=None)
            else:
                try:
                    row['real_model_attempted'] = True
                    answer = await live_answer(case['domain'],case['question'],prepared.evidence,previous,live,case['id'])
                    row['real_model_executed'] = True
                    row['mechanism'] = 'agentflow_v2' if live=='flowise' else 'real_model_draft_review'
                except Exception as exc:
                    row.update(status='inference_error',error_type=type(exc).__name__,answer=None,automated_checks_passed=False)
        if answer is not None:
            answer += department_note(case['question'],answer)
            row.update(status='answered',answer=answer,**assess(case,answer))
            formatted=matrix_content(answer)
            row['formatted_body_available']=bool(formatted.get('formatted_body'))
        row['elapsed_ms']=round((time.monotonic()-started)*1000,1)
        results.append(row)
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=ROOT/'config/helpfulness-evaluation.json')
    parser.add_argument('--include-retrieval-cases',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',choices=['direct','flowise'])
    parser.add_argument('--archive',action='store_true')
    args=parser.parse_args()
    cases=json.loads(args.cases.read_text())['cases']
    if args.include_retrieval_cases:
        cases += json.loads((ROOT/'config/answer-evaluation.json').read_text())['cases']
    seen=set();unique=[]
    for case in cases:
        key=(case['domain'],case['question'],tuple(case.get('previous_questions',[])))
        if key not in seen:unique.append(case);seen.add(key)
    rows=asyncio.run(evaluate(unique,KnowledgeStore(ROOT/'offline_knowledge'),args.live))
    summary={'scenarios':len(rows),'status_counts':dict(Counter(r['status'] for r in rows)),
             'answered_passed_automatic_checks':sum(r.get('automated_checks_passed') is True for r in rows),
             'answered_flagged':sum(r.get('status')=='answered' and r.get('automated_checks_passed') is False for r in rows),
             'real_model_executions':sum(r['real_model_executed'] for r in rows)}
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'summary':summary,'current_replies':rows,
            'historical_saved_model_review':archive_review() if args.archive else None,
            'matrix_messages_sent':False,'vm_deployed':False,
            'limits':'Automatic content patterns detect omissions/deflections, not semantic or legal correctness. Source-bound answers bypass the model. Missing live-model answers are untested, never counted as successful.'}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))
    for row in rows:
        if row.get('status')=='answered' and not row.get('automated_checks_passed'):
            print(json.dumps({'id':row['id'],'issues':row['automated_issues']},ensure_ascii=False))
    raise SystemExit(1 if summary['answered_flagged'] or summary['status_counts'].get('inference_error') else 0)

if __name__=='__main__':
    main()
