"""Export only public case IDs, numeric measurements and bounded issue codes."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'matrix_support_bot'))
from answer_usefulness import answer_body


def percentile(values,fraction):
    values=sorted(v for v in values if type(v) in (int,float) and math.isfinite(v) and 0<=v<=3600000)
    return round(values[max(0,math.ceil(len(values)*fraction)-1)],3) if values else None


def summarize(report,cases,observations):
    by_id={c['id']:c for c in cases['cases']}
    rows=[];errata=[]
    for r in report['rows']:
        c=by_id[r['id']]
        core=answer_body(r.get('answer') or '')
        checks=r.get('checks',{})
        rows.append({'id':r['id'],'domain':r['domain'],'criteria_scope':c.get('criteria_scope'),
            'automatic_passed':checks.get('passed',False),
            'required_forbidden_passed':bool(r.get('answer')) and not (checks.get('missing_conditions') or checks.get('forbidden_claims')),
            'missing_conditions':checks.get('missing_conditions',[]),'forbidden_claims':checks.get('forbidden_claims',[]),
            'usefulness_issues':checks.get('usefulness_issues',[]),
            'seconds':r.get('seconds'),'generation_unavailable':r.get('generation_unavailable',False),
            'validation_fallback':r.get('validation_fallback',False),'reply_count':r.get('reply_count',0),
            'technical_error':r.get('error') if re.fullmatch(r'[a-zA-Z_0-9]{1,80}',r.get('error') or '') else None})
        if c.get('criteria_scope')=='exact_arithmetic' and not checks.get('passed'):
            expected=c['candidate_reference'].replace('.',',')
            compact=re.sub(r'[\s\u00a0\u202f]+','',core)
            if expected in compact:
                errata.append({'id':r['id'],'code':'grouped_decimal_not_accepted_by_regex','original_score_preserved':True})
        if c.get('origin_case_id')=='unseen-20261025-23' and re.search(r'16\s*лет',core):
            errata.append({'id':r['id'],'code':'numeric_age_not_accepted_by_lexical_subject_regex','original_score_preserved':True})
    per_scope={scope:{'scenarios':sum(r['criteria_scope']==scope for r in rows),
        'automatic_passed':sum(r['criteria_scope']==scope and r['automatic_passed'] for r in rows)} for scope in sorted({r['criteria_scope'] or '' for r in rows})}
    return {'schema_version':1,'status':report['status'],'summary':report['summary'],
        'question_messages':report.get('question_messages'), 'answers_exported':False,
        'case_set_sha256':hashlib.sha256(json.dumps(cases,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        'tested_image':observations.get('tested_image',observations.get('production_image')),'criteria_scopes':per_scope,
        'criteria_errata':errata,'rows':rows,
        'preparation_mechanisms':observations.get('preparation_mechanisms',{}),
        'model_completions':observations.get('model_completions',0),
        'human_specialist_approvals':0,'expert_accuracy':None,
        'limitations':['Synthetic cases, including reused regressions; not independent expert evaluation.',
            'Delivery-only cases do not establish factual accuracy.',
            'Unchanged text criteria and diagnostic errata are reported separately.',
            'Two concurrent handlers and reset commands affect timing; not a production SLA.']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--cases',type=Path,required=True)
    p.add_argument('--observations',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();value=summarize(json.loads(a.report.read_text()),json.loads(a.cases.read_text()),json.loads(a.observations.read_text()))
    a.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');a.output.chmod(0o600)
    print(json.dumps({'summary':value['summary'],'model_completions':value['model_completions'],'criteria_errata':value['criteria_errata']}))


if __name__=='__main__':main()
