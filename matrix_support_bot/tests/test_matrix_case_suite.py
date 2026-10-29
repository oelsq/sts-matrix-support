"""Protect the campaign's request correlation and honest aggregate reporting."""
import importlib.util
import pytest
from pathlib import Path

from matrix_inbox import InboxStore

ROOT=Path(__file__).resolve().parents[2]
if ROOT == Path('/') and not (ROOT/'scripts/matrix_case_suite.py').is_file():
    pytest.skip('Repository-level campaign tooling is not shipped in the standalone bot image', allow_module_level=True)
spec=importlib.util.spec_from_file_location('matrix_case_suite',ROOT/'scripts/matrix_case_suite.py')
suite=importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


def test_final_reply_key_matches_persistent_production_outbox(tmp_path):
    import hashlib
    user='@sts-cases-bot-test:demo.local'
    identity=hashlib.sha256(('http://homeserver:8008\0'+user).encode()).hexdigest()
    store=InboxStore(tmp_path/'inbox.sqlite3',identity)
    try:
        assert suite.final_key(user,'$request')==store.transaction_id('$request','final')
        assert suite.final_key(user,'$request')!=store.transaction_id('$request','urgent')
        assert suite.final_key(user,'$request')!=suite.final_key('@other:demo.local','$request')
        assert suite.final_key(user,'$request')!=suite.final_key(user,'$other')
    finally:store.close()


def test_summary_separates_delivery_pass_fallback_and_unexecuted():
    rows=[{'domain':'legal','answer':'Example','seconds':1,'checks':{'passed':True},'reply_count':1},
          {'domain':'finance','answer':'Fallback','seconds':8,'checks':{'passed':False},'reply_count':2,'validation_fallback':True},
          {'domain':'legal','answer':None,'error':'reply_timeout'}]
    result=suite.summarize(rows,12)
    assert result['planned']==12 and result['completed']==3 and result['answered']==2
    assert result['automatic_passed']==1 and result['validation_fallback']==1
    assert result['duplicates']==1 and result['errors']=={'reply_timeout':1}
    assert result['p95_seconds']==8
    assert result['expert_accuracy'] is None and result['human_specialist_approvals']==0


def test_empty_campaign_does_not_manufacture_latency_or_success():
    result=suite.summarize([],280)
    assert result['completed']==result['answered']==result['automatic_passed']==0
    assert result['p50_seconds'] is None and result['p95_seconds'] is None


def test_published_cases_match_preexecution_freeze():
    import hashlib,json
    path=ROOT/'config/matrix-cases-20261029.json'
    data=json.loads(path.read_text());freeze=json.loads((ROOT/'config/matrix-cases-20261029-freeze.json').read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest()==freeze['sha256']
    assert len(data['cases'])==freeze['cases']==280
    assert len({c['id'] for c in data['cases']})==280
    assert {c['domain'] for c in data['cases']}=={'legal','finance'}
    assert all(c['reference_status']=='awaiting_specialist' for c in data['cases'])
