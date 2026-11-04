"""An evaluator-format retry must preserve findings and never fake approval."""
import asyncio,copy,importlib.util,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from evaluation_core import digest

ROOT=Path(__file__).parents[2]
if not (ROOT/'scripts/review_synthetic_answers.py').exists():
    pytest.skip('Repository evaluator tooling',allow_module_level=True)
spec=importlib.util.spec_from_file_location('assessment_retry',ROOT/'scripts/review_synthetic_answers.py')
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)


def finding(source_ids):
    return {'findings':[{'code':'unsupported_rule','severity':'major','answer_unit':'A1','source_ids':source_ids,'rationale':'Synthetic test rationale.'}],'limitations':'AI assessment only.'}


@pytest.mark.parametrize('responses,expected_status,expected_calls',[
    ([finding(['S1'])],'assessed',1),
    ([finding([]),finding(['S1'])],'assessed',2),
    ([finding([]),finding([])],'assessment_error',2),
    (['transport_failure'],'assessment_error',1),
])
def test_one_structure_retry_preserves_the_bot_answer_and_valid_findings(monkeypatch,tmp_path,responses,expected_status,expected_calls):
    import aiohttp,llm_settings
    calls=[];values=iter(responses)
    class Response:
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        def raise_for_status(self):pass
        async def json(self):
            value=next(values)
            if value=='transport_failure':raise RuntimeError('Synthetic transport error')
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
    class Session:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        def post(self,url,**kw):
            calls.append(copy.deepcopy(kw['json']));return Response()
    monkeypatch.setattr(aiohttp,'ClientSession',Session)
    monkeypatch.setattr(llm_settings,'api_key',lambda:'fake-test-key')
    monkeypatch.setenv('LLM_ENDPOINT','http://litellm:4000/v1')
    monkeypatch.setenv('LLM_ALLOWED_HOSTS','litellm')
    monkeypatch.setenv('LLM_MODEL','synthetic-model')
    case={'id':'synthetic-case','question':'Synthetic question','previous_questions':[]}
    private_answer='SYNTHETIC_PRIVATE_ANSWER_TOKEN [doc:1]'
    source={'status':'complete','rows':[{'id':case['id'],'case_sha256':digest(case),'answer':private_answer,
        'evidence':[{'id':'doc:1','document':'doc','section':'clause','text':'Synthetic source text','source':'https://example.test','version':'test'}]}]}
    run=tmp_path/'input.json';run.write_text(json.dumps(source))
    cases=tmp_path/'cases.json';cases.write_text(json.dumps({'cases':[case]}))
    args=SimpleNamespace(run=run,cases=cases,output=tmp_path/'private.json',summary=tmp_path/'summary.json',ids=None)
    asyncio.run(review.run(args))
    result=json.loads(args.output.read_text())['rows'][0]
    assert result['status']==expected_status and len(calls)==expected_calls
    assert result['assessment_attempts']==expected_calls
    if expected_status=='assessed':
        assert result['assessment']['findings'][0]['code']=='unsupported_rule'
        assert result['assessment']['findings'][0]['source_ids']==['S1']
    if expected_calls==2:
        assert calls[0]['messages']==calls[1]['messages'][:2]
        assert result['invalid_assessment_codes']==['Normative finding needs a source identifier']
        assert 'SYNTHETIC_PRIVATE_ANSWER_TOKEN' in calls[1]['messages'][1]['content']
    summary=json.loads(args.summary.read_text())
    assert summary['human_specialist_approvals_created']==0
    assert 'SYNTHETIC_PRIVATE_ANSWER_TOKEN' not in args.summary.read_text()
