#!/usr/bin/env python3
"""Compare frozen and proposed criteria on the same private replies; export no reply text."""
import argparse,copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from evaluation_core import automated_checks,digest,private_json


def compare(cases,run,proposals):
    if run.get('status')!='complete':raise ValueError('Use a complete private evaluation')
    by_id={c['id']:c for c in cases};actual={r['id']:r for r in run['rows']}
    if len(actual)!=len(run['rows']) or set(actual)!=set(by_id):raise ValueError('Case coverage changed')
    updates=proposals['proposals']
    if not set(updates).issubset(by_id):raise ValueError('Unknown proposed case')
    rows=[]
    for ident,case in by_id.items():
        row=actual[ident]
        if row.get('case_sha256')!=digest(case):raise ValueError('Original case changed after execution')
        candidate=copy.deepcopy(case)
        if ident in updates:
            change=updates[ident]
            if change['original_case_sha256']!=digest(case):raise ValueError('Proposal was prepared for another case')
            if not set(change['replace_required']).issubset(case['required']) or not set(change['remove_required']).issubset(case['required']):raise ValueError('Unknown criterion')
            for name in change['remove_required']:candidate['required'].pop(name)
            candidate['required'].update(change['replace_required'])
        evidence=[{'document_id':p['document']} for p in row.get('evidence',[])]
        old=automated_checks(case,row['answer'],evidence) if row.get('answer') else None
        new=automated_checks(candidate,row['answer'],evidence) if row.get('answer') else None
        rows.append({'case_id':ident,'original_passed':bool(old and old['passed']),'proposed_passed':bool(new and new['passed']),
                     'original_missing':old['missing_conditions'] if old else [],'proposed_missing':new['missing_conditions'] if new else [],'generation_error_class':row.get('error')})
    return {'schema_version':1,'status':'automatic_comparison_not_expert_review','same_private_answers':True,'answers_copied':False,'expert_approvals_created':0,
            'total':len(rows),'original_passed':sum(r['original_passed'] for r in rows),'proposed_passed':sum(r['proposed_passed'] for r in rows),'rows':rows}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cases',type=Path,default=ROOT/'config/model-challenge-cases.json');p.add_argument('--run',type=Path,required=True);p.add_argument('--proposals',type=Path,default=ROOT/'config/answer-criteria-proposals.json');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=compare(json.loads(a.cases.read_text())['cases'],json.loads(a.run.read_text()),json.loads(a.proposals.read_text()));private_json(a.output,report);print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
if __name__=='__main__':main()
