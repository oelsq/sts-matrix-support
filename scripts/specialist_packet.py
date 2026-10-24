#!/usr/bin/env python3
"""Export or import a private review packet bound to exact questions, corpus and answers."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from evaluation_core import corpus_digest,private_json
from expert_review import export_packet,import_reviews

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cases',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--knowledge',type=Path,default=ROOT/'offline_knowledge')
 sub=p.add_subparsers(dest='command',required=True);e=sub.add_parser('export');e.add_argument('--output',type=Path,required=True);e.add_argument('--metadata-only',action='store_true',help='Copy no answer text; reference existing private run by exact answer hash');i=sub.add_parser('import');i.add_argument('--completed-template',type=Path,required=True);i.add_argument('--output',type=Path,required=True)
 a=p.parse_args();cases=json.loads(a.cases.read_text())['cases'];run=json.loads(a.run.read_text());corpus=corpus_digest(a.knowledge)
 if a.command=='export':print(json.dumps(export_packet(cases,run,a.output,corpus=corpus,knowledge_root=a.knowledge,include_answers=not a.metadata_only)));return
 records=import_reviews(cases,run,json.loads(a.completed_template.read_text()),corpus=corpus);previous=json.loads(a.output.read_text()) if a.output.exists() else [];private_json(a.output,previous+records);print(json.dumps({'imported_operator_confirmed_reviews':len(records),'identity_independently_authenticated':False}))
if __name__=='__main__':main()
