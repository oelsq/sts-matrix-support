#!/usr/bin/env python3
"""Evaluate candidate reference cases; specialist scores are recorded, never inferred."""
import argparse,asyncio,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'matrix_support_bot'),str(ROOT/'scripts')]
from evaluation_core import corpus_digest,digest,private_json,automated_checks,current_reviews,record_review,summarize

async def run(args):
    from offline_knowledge import KnowledgeStore
    from local_reply import prepare_local_reply
    from evaluate_answers import case_context
    from evaluate_helpfulness import live_answer
    cases=json.loads(args.cases.read_text())['cases'];fingerprint=corpus_digest(args.knowledge)
    records=json.loads(args.reviews.read_text()) if args.reviews and args.reviews.exists() else []
    store=KnowledgeStore(args.knowledge);rows=[]
    for case in cases:
        started=time.monotonic();search,previous=case_context(case)
        prepared=prepare_local_reply(case['domain'],case['question'],store,search_question=search,previous_question=previous)
        answer=prepared.answer;mechanism=prepared.mechanism;error=None
        if answer is None and args.live:
            try:answer=await live_answer(case['domain'],case['question'],prepared.evidence,previous,args.live,case['id']);mechanism='agentflow_v2' if args.live=='flowise' else 'direct_model'
            except Exception as exc:error=type(exc).__name__
        row={'id':case['id'],'case_sha256':digest(case),'domain':case['domain'],'question':case['question'],'candidate_reference':case['candidate_reference'],'answer':answer,'mechanism':mechanism,'error':error,
             'evidence':[{'id':p['id'],'document':p['document_id'],'source':p['source'],'version':p.get('version'),'section':p.get('section'),'text':p['text']} for p in prepared.evidence or []],
             'checks':automated_checks(case,answer,prepared.evidence) if answer else None,
             'reviews':current_reviews(case,fingerprint,answer,records),'milliseconds':round((time.monotonic()-started)*1000,2)}
        rows.append(row)
    report={'schema_version':1,'corpus_sha256':fingerprint,'rows':rows,'summary':summarize(rows),'external_specialist_approval_required':True}
    private_json(args.output,report);print(json.dumps(report['summary'],ensure_ascii=False))
    return 1 if any(r['error'] or r['checks'] and not r['checks']['passed'] for r in rows) else 0

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cases',type=Path,default=ROOT/'config/golden-cases.json');p.add_argument('--knowledge',type=Path,default=ROOT/'offline_knowledge')
    sub=p.add_subparsers(dest='command',required=True)
    e=sub.add_parser('run');e.add_argument('--reviews',type=Path);e.add_argument('--output',required=True,type=Path);e.add_argument('--live',choices=['direct','flowise'])
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
