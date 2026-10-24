#!/usr/bin/env python3
"""Build a local index from integrity-checked corpus; no query text or network access."""
import argparse,os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from offline_knowledge import KnowledgeStore
from semantic_search import LocalEncoder,build_index

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--knowledge',type=Path,default=ROOT/'offline_knowledge');p.add_argument('--model',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--threads',type=int,default=2);a=p.parse_args()
 os.environ.pop('SEMANTIC_INDEX_DIR',None);store=KnowledgeStore(a.knowledge);encoder=LocalEncoder(a.model,a.threads)
 print(json.dumps(build_index(store.passages,encoder,a.output,progress=lambda n,total:print(json.dumps({'indexed':n,'total':total}),flush=True))))
if __name__=='__main__':main()
