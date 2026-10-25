#!/usr/bin/env python3
"""Export category totals from opt-in feedback; never export people or reply text."""
import argparse,json,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from evaluation_core import private_json


def report(path, *, now=None):
    cutoff=datetime.fromtimestamp((now if now is not None else datetime.now(timezone.utc).timestamp())-90*86400,timezone.utc).date().isoformat()
    with sqlite3.connect('file:'+quote(str(Path(path).resolve()),safe='/')+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='answer_feedback'").fetchone()
        rows=db.execute('SELECT domain,category,count(*) FROM answer_feedback WHERE day>=? GROUP BY domain,category',(cutoff,)).fetchall() if exists else []
    result={'status':'voluntary_user_feedback_not_expert_accuracy','window_days':90,'by_domain':{},'invalid_metadata_count':0,'answer_or_question_text_exported':False}
    for domain,category,count in rows:
        if domain not in {'legal','finance','general','mixed'} or category not in {'helpful','incomplete','suspected_error','unhelpful'}:
            result['invalid_metadata_count']+=count;continue
        result['by_domain'].setdefault(domain,{})[category]=count
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inbox',required=True,type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    result=report(a.inbox)
    if a.output:private_json(a.output,result)
    print(json.dumps(result))
if __name__=='__main__':main()
