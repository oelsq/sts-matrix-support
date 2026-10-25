import asyncio
import copy
import json
from pathlib import Path
import pytest
from workflow_metrics import execution_metrics,PredictionText


def node(ident='llmAgentflow_0',duration=1500):
    return {'nodeId':ident,'nodeLabel':'PRIVATE LABEL','data':{'input':'PRIVATE INPUT','state':{'secret':'PRIVATE STATE'},'output':{'content':'PRIVATE ANSWER','timeMetadata':{'start':1700000000000,'end':1700000001500,'delta':duration},'responseMetadata':{'secret':'PRIVATE KEY'}}}}


def test_stage_metrics_are_allowlisted_numeric_and_exclude_all_content_and_timestamps():
    raw={'agentFlowExecutedData':[node('agentAgentflow_0',1000),node(),node('llmAgentflow_1',700),node('llmAgentflow_2',1200),node('attacker',10)]}
    metrics=execution_metrics(raw)
    assert metrics['model_nodes_executed']==4 and metrics['repair_used'] is True
    assert metrics['stages']['audit']['milliseconds']==1500
    assert metrics['stages']['repair']['calls']==1
    encoded=json.dumps(metrics)
    for forbidden in ['PRIVATE','secret','1700000000000','attacker','content','input','nodeLabel']:
        assert forbidden not in encoded
    assert PredictionText('unchanged answer',metrics)=='unchanged answer'


@pytest.mark.parametrize('duration',[None,True,-1,3600001,float('nan'),float('inf'),'1500'])
def test_missing_or_invalid_duration_is_not_reported_as_zero(duration):
    m=execution_metrics({'agentFlowExecutedData':[node(duration=duration)]})
    assert m['stages']['audit']['milliseconds'] is None
    assert m['stages']['audit']['timed_calls']==0
    assert m['model_nodes_executed']==1


@pytest.mark.parametrize('value',[None,{},'private',[],[node()]*65])
def test_missing_or_oversized_execution_statistics_never_invent_model_calls(value):
    m=execution_metrics({'agentFlowExecutedData':value})
    assert m['available'] is False and m['model_nodes_executed'] is None and m['repair_used'] is None


def test_repeated_known_stage_aggregates_calls_and_partial_timing():
    m=execution_metrics({'agentFlowExecutedData':[node(duration=0),node(duration=10.25),node(duration=None),None]})
    assert m['stages']['audit']=={'calls':3,'timed_calls':2,'milliseconds':10.25}


def assessor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    import review_synthetic_answers
    return review_synthetic_answers


def packet_row():
    return {'answer':'**Юридический агент**\nПравило действует при условии. [law:p2:1]\nСохраните договор.\n\nЛокальные материалы для проверки:\nPRIVATE APPENDIX MAY LOOK LIKE A CLAIM',
            'evidence':[{'id':'law:p2:1','document':'law','text':'Exact public source condition','source':'https://example.test/law','section':'Section','version':'Verified version'}]}


def test_assessment_excludes_source_appendix_and_maps_rendered_citation_exactly(monkeypatch):
    a=assessor(monkeypatch);row=packet_row();before=copy.deepcopy(row)
    p=a.assessment_packet({'question':'question','previous_questions':['previous']},row)
    assert list(p['answer_units'])==['A1','A2']
    assert p['answer_units']['A1'].endswith('[S1]')
    assert 'PRIVATE APPENDIX' not in json.dumps(p)
    assert p['sources']['S1']['fragment_id']=='law:p2:1'
    assert p['sources']['S1']['version']=='Verified version'
    assert p['previous_questions']==['previous'] and row==before


def test_unknown_citations_and_document_placeholders_are_not_silently_approved(monkeypatch):
    a=assessor(monkeypatch);row=packet_row();row['answer']='Прошу проверить [ФИО] [unknown:p1] [S9]. [law:p2:1]'
    body=a.assessment_packet({'question':'q'},row)['answer_units']['A1']
    assert '[ФИО]' in body and '[unknown:p1]' in body and '[S9]' in body and '[S1]' in body


@pytest.mark.parametrize('change',['duplicate','missing_id','bracket','non_dict','no_text','too_many','empty_body'])
def test_invalid_assessment_packet_is_rejected(monkeypatch,change):
    a=assessor(monkeypatch);row=packet_row()
    if change=='duplicate':row['evidence']*=2
    if change=='missing_id':row['evidence'][0].pop('id')
    if change=='bracket':row['evidence'][0]['id']='[forged]'
    if change=='non_dict':row['evidence']=['private']
    if change=='no_text':row['evidence'][0]['text']=None
    if change=='too_many':row['evidence']*=9
    if change=='empty_body':row['answer']='**Юридический агент**'
    with pytest.raises(ValueError):a.assessment_packet({'question':'q'},row)


def test_extra_assessment_approval_fields_are_never_accepted(monkeypatch):
    a=assessor(monkeypatch)
    with pytest.raises(ValueError):a.validate_assessment({'findings':[],'limitations':'Unknown','approved_by_human':True},{},{})


def test_prediction_keeps_numeric_metrics_without_revealing_payload(caplog):
    from test_flowise_client import HTTP,Response,client,ROOM
    result={'text':'Unchanged answer','agentFlowExecutedData':[node('agentAgentflow_0',1200),node('llmAgentflow_0',800)]}
    http=HTTP(Response(chunks=[json.dumps(result).encode()]))
    p=asyncio.run(client()._prediction(http,'flow','PRIVATE PROMPT','opaque',75))
    assert p=='Unchanged answer' and p.workflow_metrics['model_nodes_executed']==2
    assert p.workflow_metrics['stages']['draft']['milliseconds']==1200
    assert 'PRIVATE' not in caplog.text


def test_quality_reply_retains_metrics_on_success_and_review_failure():
    from quality_reply import quality_reply,EvaluatedAnswer
    from flowise_client import FlowiseReviewRejected
    class Flow:
        def __init__(self,reject):self.reject=reject
        async def run_workflow(self,*args,**kwargs):
            if self.reject:
                e=FlowiseReviewRejected('Safe failure');e.workflow_metrics={'elapsed_ms':321,'stages':{}};raise e
            return PredictionText('Сохраните договор.',{'elapsed_ms':123,'stages':{}})
    # General direction avoids source rendering and isolates transport metadata.
    async def run():
        q=await quality_reply(Flow(False),None,'general','room','user','event','q','',None,None)
        assert EvaluatedAnswer(q).workflow_metrics==[{'elapsed_ms':123,'stages':{},'attempt':1}]
        with pytest.raises(FlowiseReviewRejected) as e:await quality_reply(Flow(True),None,'general','room','user','event','q','',None,None)
        assert e.value.workflow_metrics==[{'elapsed_ms':321,'stages':{},'attempt':1}]
    asyncio.run(run())


def test_custom_function_measurement_preserves_result_and_propagates_errors():
    import subprocess
    from agentflow_quality_graph import timed_custom_function
    wrapped=timed_custom_function('return {audit_ok:"no",response:"same"};')
    value=json.loads(subprocess.check_output(['node','-e','process.stdout.write(JSON.stringify((()=>{'+wrapped+'})()))'],text=True))
    assert value['audit_ok']=='no' and value['response']=='same'
    assert value['timeMetadata']['end']>=value['timeMetadata']['start']
    code='try{(()=>{'+timed_custom_function('throw Error("closed failure");')+'})()}catch(e){process.stdout.write("propagated");}'
    assert subprocess.check_output(['node','-e',code],text=True)=='propagated'


@pytest.mark.parametrize('q,expected',[
    ('Мне 16 лет, предлагают подписать договор подработки.',True),
    ('Мне 15 лет. Можно устроиться на работу по трудовому договору?',True),
    ('Подросток хочет работать, какой трудовой договор проверить?',True),
    ('Мне 18 лет, предлагают подписать договор подработки.',False),
    ('Мне 16 лет, предлагают подписать договор вклада.',False),
    ('Мне 16 лет, как оформить кредит для подработки?',False),
    ('Мне 16 лет, предлагают договор ГПХ на подработку.',False),
])
def test_minor_employment_routing_is_subject_specific(q,expected):
    from retrieval_intents import minor_employment_question,query_hints
    assert minor_employment_question(q)==expected
    if expected:
        expansion,docs,sections=query_hints(q)
        assert 'labor' in docs and {'Статья 31.','Статья 26.','Статья 69.'}<=set(sections['labor'])


def test_flowise_custom_content_time_is_read_without_exporting_the_content():
    n=node('customFunctionAgentflow_2');n['data']['output']={'content':json.dumps({'audit_ok':'no','quality_json':'PRIVATE SOURCE','timeMetadata':{'start':123,'end':130,'delta':7}})}
    metrics=execution_metrics({'agentFlowExecutedData':[n]})
    assert metrics['stages']['gate']['milliseconds']==7
    assert 'PRIVATE' not in json.dumps(metrics) and 'audit_ok' not in json.dumps(metrics)
    n['nodeId']='llmAgentflow_0'
    assert execution_metrics({'agentFlowExecutedData':[n]})['stages']['audit']['milliseconds'] is None


@pytest.mark.parametrize('content',['not-json','[1,2]','{"timeMetadata":true}','x'*512001])
def test_custom_content_without_valid_metadata_stays_unmeasured(content):
    n=node('customFunctionAgentflow_0');n['data']['output']={'content':content}
    assert execution_metrics({'agentFlowExecutedData':[n]})['stages']['prepare']['milliseconds'] is None


@pytest.mark.parametrize('word,value',[
    ('двенадцатилетнего',12),('тринадцатилетнему',13),('четырнадцатилетний',14),
    ('пятнадцатилетними',15),('шестнадцатилетнего',16),('семнадцатилетней',17),('восемнадцатилетних',18)])
def test_written_age_and_digits_have_identical_quantities_in_python_and_javascript(word,value):
    import subprocess
    from agentflow_claims import quantities
    from agentflow_quality_graph import unit_code
    from numeric_grounding import normalize_age_words
    assert quantities(word)==quantities(str(value)+' лет')=={str(float(value))}
    assert normalize_age_words('до '+word+' возраста')=='до '+str(value)+' лет возраста'
    script=unit_code()+'\nprocess.stdout.write(JSON.stringify([...quantities('+json.dumps(word)+')]));'
    assert set(json.loads(subprocess.check_output(['node','-e',script],text=True)))=={str(value)}


@pytest.mark.parametrize('age,accepted',[(16,True),(15,False),(17,False)])
def test_age_equivalence_never_approves_another_age_or_changes_quote(age,accepted):
    import subprocess
    from agentflow_claims import validate_audit
    from agents import unsupported_quantities
    source='Заключение трудового договора допускается с гражданами, достигшими шестнадцатилетнего возраста.'
    answer=f'Заключение трудового договора допускается с гражданами с {age} лет. [S1]'
    audit={'verdict':'approve','checks':[{'unit_id':'C1','status':'supported','proofs':[{'source_id':'S1','quote':source}],'standalone':True}]}
    assert (not validate_audit(answer,audit,[{'id':'S1','text':source}]))==accepted
    assert (not unsupported_quantities(answer,'С какого возраста можно заключать договор?',[{'id':'S1','text':source,'version':''}],{'S1'}))==accepted
    quality={'question_type':'situation','question':'q','previous_question':'','sources':[{'id':'S1','text':source}]}
    body=(Path(__file__).resolve().parents[1]/'agents/quality/claim_gate.js').read_text()
    script='const $flow='+json.dumps({'state':{'draft':answer,'quality_json':json.dumps(quality)}})+';const $audit='+json.dumps(json.dumps(audit))+';process.stdout.write(JSON.stringify((()=>{'+body+'})()));'
    assert (json.loads(subprocess.check_output(['node','-e',script],text=True))['audit_ok']=='yes')==accepted


@pytest.mark.parametrize('text',['нешестнадцатилетнего','шестнадцатилетнийюбилей','xшестнадцатилетнего','βшестнадцатилетнего','әшестнадцатилетнего'])
def test_age_word_fragments_never_create_supported_numbers(text):
    from numeric_grounding import normalize_age_words
    assert normalize_age_words(text)==text
