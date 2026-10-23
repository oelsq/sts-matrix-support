#!/usr/bin/env python3
"""Record a real specialist decision or validate/promote that exact reviewed snapshot."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from knowledge_updates import approve_candidate,promote
from evaluation_core import private_json
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--knowledge',type=Path,default=ROOT/'offline_knowledge');p.add_argument('--receipt',type=Path,required=True);p.add_argument('--approval',type=Path,required=True);p.add_argument('--record-review',action='store_true');p.add_argument('--reviewer');p.add_argument('--version');p.add_argument('--applicability-note');p.add_argument('--apply',action='store_true');p.add_argument('--scope-reviewed',action='store_true');a=p.parse_args()
if a.record_review:
 if a.apply or not all([a.reviewer,a.version,a.applicability_note]):p.error('Supply reviewer, version and applicability assessment; promotion is separate')
 receipt=json.loads(a.receipt.read_text());item=next(d for d in json.loads((a.knowledge/'manifest.json').read_text())['documents'] if d['id']==receipt['id']);artifact=(a.receipt.parent/receipt['artifact_file']).resolve()
 if not artifact.is_relative_to(a.receipt.parent.resolve()):p.error('Candidate escapes staging')
 private_json(a.approval,approve_candidate(item,receipt,artifact,reviewer=a.reviewer,version=a.version,applicability_note=a.applicability_note));print('Operator-supplied specialist review recorded')
else:print(json.dumps(promote(a.knowledge,a.receipt,a.approval,apply=a.apply,scope_reviewed=a.scope_reviewed)))
