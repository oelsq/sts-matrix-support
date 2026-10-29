"""Prepare a closed source assessment from delivered synthetic answers only."""
import argparse
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,'/app')
from evaluation_core import digest,private_json
from offline_knowledge import KnowledgeStore


def delivered_sources(answer, passages):
    # Matrix converts fragment citations to [1], [2] and moves their exact IDs
    # to the source cards. Preserve that bridge instead of inventing evidence.
    refs = re.findall(r'Фрагмент:\s*([^\n]+?)\.(?=\s|$)', answer)
    if len(refs) > 8 or len(set(refs)) != len(refs):
        raise ValueError('Invalid delivered source mapping')
    if any(ident not in passages for ident in refs):
        raise ValueError('Delivered fragment not present in the same corpus')
    if 'Цитата из документа:' in answer and not refs:
        raise ValueError('Source cards lack exact fragment identifiers')
    def alias(match):
        i=int(match[1]);return '['+refs[i-1]+']' if 1<=i<=len(refs) else match[0]
    answer=re.sub(r'\[([1-8])\]',alias,answer)
    evidence=[]
    for ident in refs:
        p=passages[ident]
        evidence.append({'id':ident,'document':p['document_id'],'source':p['source'],
            'version':p.get('version'),'section':p.get('section'),'text':p['text']})
    return answer,evidence


def main():
    p=argparse.ArgumentParser();p.add_argument('--ids',required=True);p.add_argument('--output-prefix',required=True)
    a=p.parse_args();stage=Path('/stage')
    source=json.loads((stage/'matrix-case-results.json').read_text())
    cases={c['id']:c for c in json.loads((stage/'cases.json').read_text())['cases']}
    selected=set(a.ids.split(','));assert selected.issubset(cases)
    store=KnowledgeStore('/app/offline_knowledge');passages={p['id']:p for p in store.passages}
    rows=[];used=[]
    for original in source['rows']:
        if original['id'] not in selected or not original.get('answer'):continue
        case=cases[original['id']]
        answer,evidence=delivered_sources(original['answer'],passages)
        rows.append({'id':case['id'],'case_sha256':digest(case),'answer':answer,'evidence':evidence})
        used.append(case)
    assert rows
    private_json(stage/(a.output_prefix+'-input.json'),{'status':'complete','rows':rows})
    private_json(stage/(a.output_prefix+'-cases.json'),{'cases':used})
    print(json.dumps({'prepared':len(rows),'sources_per_case':{r['id']:len(r['evidence']) for r in rows}}))


if __name__=='__main__':main()
