#!/usr/bin/env python3
"""Inspect offline integrity/review age; optionally stage selected official sources."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from knowledge_updates import inventory,check_source
from evaluation_core import private_json

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--knowledge',type=Path,default=ROOT/'offline_knowledge');p.add_argument('--output',type=Path,required=True);p.add_argument('--online',help='Comma-separated document IDs; no unrestricted crawling');p.add_argument('--staging',type=Path)
 a=p.parse_args();report=inventory(a.knowledge)
 if a.online:
  if not a.staging:p.error('--staging required for online checks')
  requested=set(a.online.split(','));items=json.loads((a.knowledge/'manifest.json').read_text())['documents']
  if requested-{d['id'] for d in items}:p.error('Unknown document IDs')
  report['online']=[check_source(d,a.staging) for d in items if d['id'] in requested]
 private_json(a.output,report);print(json.dumps({'inventory':report['counts'],'online':[{k:r.get(k) for k in ['id','status','error_type']} for r in report.get('online',[])]},ensure_ascii=False))
 raise SystemExit(1 if any(not r['integrity'] for r in report['documents']) else 0)
if __name__=='__main__':main()
