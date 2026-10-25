#!/usr/bin/env python3
"""Evaluate candidate reference cases; specialist scores are recorded, never inferred."""
import argparse,asyncio,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'matrix_support_bot'),str(ROOT/'scripts')]
from evaluation_core import corpus_digest,digest,private_json,automated_checks,current_reviews,record_review,summarize

async def run(args):
    if getattr(args,'force_model',False) and not args.live:raise ValueError('--force-model requires --live; offline text is never counted as inference')
    from offline_knowledge import KnowledgeStore
    from local_reply import prepare_local_reply, prepare_model_evidence
    from evaluate_answers import case_context
    from evaluate_helpfulness import live_answer
    cases=json.loads(args.cases.read_text())['cases'];fingerprint=corpus_digest(args.knowledge)
    records=json.loads(args.reviews.read_text()) if args.reviews and args.reviews.exists() else []
    store=KnowledgeStore(args.knowledge);rows=[None]*len(cases)
    concurrency=getattr(args,'concurrency',1)
    if type(concurrency) is not int or not 1<=concurrency<=2:raise ValueError('Evaluation concurrency must be 1 or 2')
    semaphore=asyncio.Semaphore(concurrency)
    async def evaluate_index(index,case):
        async with semaphore:
            started=time.monotonic();search,previous=case_context(case)
            prepared=prepare_local_reply(case['domain'],case['question'],store,search_question=search,previous_question=previous)
            answer=prepared.answer;mechanism=prepared.mechanism;error=None;error_detail=None
            forced=bool(getattr(args,'force_model',False))
            attempted=False;validation_errors=[];workflow_metrics=[]
            if forced and prepared.mechanism not in {'clarification','cashflow_calculator','no_evidence_fallback'}:
                evidence=prepare_model_evidence(case['domain'],search,store)
                if evidence:
                    prepared.evidence=evidence;answer=None;mechanism='requires_model'
                else:
                    mechanism='forced_model_skipped_no_scoped_evidence'

            if answer is None and args.live and mechanism!='forced_model_skipped_no_scoped_evidence':
                attempted=True
                try:answer=await live_answer(case['domain'],case['question'],prepared.evidence,previous,args.live,case['id'],knowledge=store);mechanism='agentflow_v2' if args.live=='flowise' else 'direct_model'
                except Exception as exc:
                    error=type(exc).__name__;error_detail=str(exc)[:300];validation_errors=list(getattr(exc,'validation_errors',()));workflow_metrics=list(getattr(exc,'workflow_metrics',[]))
            actual_evidence=getattr(answer,'evidence',prepared.evidence)
            row={'workflow_metrics':getattr(answer,'workflow_metrics',workflow_metrics),'validation_errors':validation_errors,'force_model_requested':forced,'model_attempted':attempted,'model_response_returned':attempted and answer is not None,'local_mechanism':prepared.mechanism,'research_attempts':getattr(answer,'research_attempts',0),'id':case['id'],'case_sha256':digest(case),'domain':case['domain'],'question':case['question'],'candidate_reference':case['candidate_reference'],'answer':answer,'mechanism':mechanism,'error':error,'error_detail':error_detail,
                 'evidence':[{'id':p['id'],'document':p['document_id'],'source':p['source'],'version':p.get('version'),'section':p.get('section'),'text':p['text']} for p in actual_evidence or []],
                 'checks':automated_checks(case,answer,actual_evidence) if answer else None,
                 'reviews':current_reviews(case,fingerprint,answer,records),'milliseconds':round((time.monotonic()-started)*1000,2)}
            rows[index]=row
            if args.progress:
                completed=[r for r in rows if r is not None]
                checkpoint={'schema_version':1,'status':'in_progress','planned_scenarios':len(cases),'execution_concurrency':concurrency,'corpus_sha256':fingerprint,'rows':completed,'summary':summarize(completed),'external_specialist_approval_required':True}
                private_json(args.output,checkpoint)
                print(json.dumps({'completed':len(completed),'total':len(cases),'last_case':case['id'],'error':error,'automatic_passed':row['checks']['passed'] if row['checks'] else None}),flush=True)
    await asyncio.gather(*(evaluate_index(i,case) for i,case in enumerate(cases)))
    report={'status':'complete','execution_concurrency':concurrency,'planned_scenarios':len(cases),'schema_version':1,'corpus_sha256':fingerprint,'rows':rows,'summary':summarize(rows),'external_specialist_approval_required':True}
    private_json(args.output,report);print(json.dumps(report['summary'],ensure_ascii=False))
    return 1 if any(r['error'] or r['checks'] and not r['checks']['passed'] for r in rows) else 0

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cases',type=Path,default=ROOT/'config/golden-cases.json');p.add_argument('--knowledge',type=Path,default=ROOT/'offline_knowledge')
    sub=p.add_subparsers(dest='command',required=True)
    e=sub.add_parser('run');e.add_argument('--reviews',type=Path);e.add_argument('--output',required=True,type=Path);e.add_argument('--live',choices=['direct','flowise']);e.add_argument('--progress',action='store_true');e.add_argument('--concurrency',type=int,choices=[1,2],default=1,help='Bound independent synthetic scenarios; production worker settings are unchanged');e.add_argument('--force-model',action='store_true',help='Bypass prepared explanatory text; keep clarification, arithmetic and evidence scope safeguards')
    r=sub.add_parser('review');r.add_argument('--case',required=True);r.add_argument('--kind',choices=['reference','answer'],required=True);r.add_argument('--reviewer',required=True);r.add_argument('--role',required=True,choices=['legal_specialist','finance_specialist']);r.add_argument('--decision',required=True,choices=['approved','rejected']);r.add_argument('--run',type=Path);r.add_argument('--scores',help='accuracy,usefulness,exceptions: integers 0..4');r.add_argument('--notes',default='');r.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if args.command=='run':raise SystemExit(asyncio.run(run(args)))
    case=next(c for c in json.loads(args.cases.read_text())['cases'] if c['id']==args.case);corpus=corpus_digest(args.knowledge);answer=None;scores=None
    if args.kind=='answer':
        if not args.run:p.error('--run required for answer review')
        report=json.loads(args.run.read_text())
        if report['corpus_sha256']!=corpus:p.error('Run used another corpus; re-evaluate before review')
        evaluated=next(r for r in report['rows'] if r['id']==args.case)
        if evaluated['case_sha256']!=digest(case):p.error('Case changed since evaluation; re-run first')
        answer=evaluated['answer']
        if args.scores:scores=dict(zip(['accuracy','usefulness','exceptions'],map(int,args.scores.split(','))))
    row=record_review(case,corpus,reviewer=args.reviewer,role=args.role,kind=args.kind,decision=args.decision,answer=answer,scores=scores,notes=args.notes)
    records=json.loads(args.output.read_text()) if args.output.exists() else [];records.append(row);private_json(args.output,records)
    print('Specialist decision recorded; identity is supplied by operator, not authenticated by this CLI')
if __name__=='__main__':main()
