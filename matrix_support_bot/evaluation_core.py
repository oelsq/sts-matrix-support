"""Separate automated regressions from explicitly recorded specialist judgments."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def corpus_digest(root):
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text())
    for item in manifest['documents']:
        path=(root/item['file']).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Corpus integrity check failed: '+item['id'])
    return digest(manifest)


def private_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    fd,name=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o600);json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def automated_checks(case,answer,evidence):
    from answer_usefulness import answer_body,usefulness_issues
    core=answer_body(answer);issues=usefulness_issues(case['question'],answer)
    missing=[name for name,pattern in case['required'].items() if not re.search(pattern,core,re.I|re.S)]
    forbidden=[name for name,pattern in case.get('forbidden',{}).items() if re.search(pattern,core,re.I|re.S)]
    source_hits=[source for source in case.get('sources',[]) if any(p['document_id']==source for p in evidence or [])]
    return {'passed':not (missing or forbidden or issues),'missing_conditions':missing,'forbidden_claims':forbidden,
            'usefulness_issues':issues,'source_hits':source_hits,'expert_accuracy':None}


def record_review(case,corpus,*,reviewer,role,kind,decision,answer=None,scores=None,notes=''):
    if kind not in {'reference','answer'} or decision not in {'approved','rejected'}:raise ValueError('Unknown review kind/decision')
    if role!=('legal_specialist' if case['domain']=='legal' else 'finance_specialist'):raise ValueError('Reviewer role does not match domain')
    if not reviewer.strip() or len(reviewer)>160:raise ValueError('Reviewer identity is required')
    if kind=='answer' and not answer:raise ValueError('Exact evaluated answer required')
    if kind=='answer' and (not isinstance(scores,dict) or set(scores)!={'accuracy','usefulness','exceptions'} or any(type(v)is not int or not 0<=v<=4 for v in scores.values())):
        raise ValueError('Three specialist scores between 0 and 4 required')
    if kind=='answer' and decision=='approved' and any(v<3 for v in scores.values()):raise ValueError('Low-scoring answer cannot be approved')
    return {'case_id':case['id'],'case_sha256':digest(case),'corpus_sha256':corpus,'kind':kind,
            'answer_sha256':digest(answer) if answer else None,'reviewer':reviewer.strip(),'role':role,
            'decision':decision,'scores':scores,'notes':notes,'reviewed_at':datetime.now(timezone.utc).isoformat(),
            'identity_assurance':'operator_supplied_identity_not_independently_authenticated'}


def current_reviews(case,corpus,answer,records):
    valid=[]
    for row in records:
        if row.get('case_id')!=case['id'] or row.get('case_sha256')!=digest(case) or row.get('corpus_sha256')!=corpus:continue
        try:
            expected=record_review(case,corpus,reviewer=row['reviewer'],role=row['role'],kind=row['kind'],decision=row['decision'],answer=answer if row['kind']=='answer' else None,scores=row.get('scores'),notes=row.get('notes',''))
        except (ValueError,KeyError,TypeError):continue
        if row.get('answer_sha256')==expected['answer_sha256']:valid.append(row)
    result={}
    for row in valid:result[row['kind']]=row
    return result


def summarize(rows):
    answered=[r for r in rows if r.get('answer') is not None]
    reviewed=[r for r in answered if r.get('reviews',{}).get('reference',{}).get('decision')=='approved' and 'answer' in r.get('reviews',{})]
    result={'scenarios':len(rows),'answered':len(answered),'not_executed':len(rows)-len(answered),
            'automatic_passed':sum(r['checks']['passed'] for r in answered),'specialist_scored':len(reviewed),
            'expert_accuracy':None,'expert_usefulness':None,'expert_exceptions':None}
    if reviewed:
        for key in ['accuracy','usefulness','exceptions']:
            result['expert_'+key]=round(sum(r['reviews']['answer']['scores'][key] for r in reviewed)/len(reviewed)/4,4)
    return result
