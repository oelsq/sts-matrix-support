import copy,json
from types import SimpleNamespace as NS
import pytest
from retrieval_intents import query_hints
from local_reply import prepare_model_evidence
from answer_focus import drafting_focus
from test_quality_refresh import fixtures
from agentflow_quality_refresh import refresh_plan
from agentflow_quality_graph import DRAFT
from agentflow_upgrade import apply_plan,rollback_plan

@pytest.mark.parametrize('question,forbidden',[
    ('Как оформить бумагу для коллеги?', 'state-services'),
    ('Госорган прислал информационное письмо.', 'state-services'),
    ('В магазине распечатали чек о покупке.', 'copyright'),
    ('Нарисуй мне картину моря.', 'copyright'),
    ('Посредник продаёт квартиру, сколько его комиссия?', 'mediation'),
    ('Помогите разрешить спор с продавцом.', 'mediation'),
    ('Арендую станок без права выкупа за ежемесячную плату.', 'leasing'),
    ('Автомобиль продаётся сразу за полную цену.', 'leasing'),
    ('Хозяин удерживает залог за арендованную квартиру.', 'mortgage'),
    ('Не могу платить кредит, хочу изменить график.', 'bankruptcy'),
    ('Хочу восстановить пароль для оплаты долга.', 'bankruptcy'),
    ('Как распределить прибыль между участниками ТОО?', 'joint-stock'),
])
def test_ambiguous_or_different_concepts_do_not_get_specific_statute_hint(question,forbidden):
    assert forbidden not in query_hints(question)[1]


def test_refresh_updates_first_draft_instructions_but_preserves_private_model_config_and_rollback():
    routes,flows,api=fixtures()
    for value in flows.values():
        graph=json.loads(value['flowData'])
        node=next(n for n in graph['nodes'] if n['id']=='agentAgentflow_0')
        node['data']['inputs']['agentMessages']=[{'role':'system','content':'previous instructions'}]
        value['flowData']=json.dumps(graph)
    before=copy.deepcopy(flows);plan=refresh_plan(routes,api);apply_plan(plan,routes,api,lambda _:None)
    for domain,value in flows.items():
        node=next(n for n in json.loads(value['flowData'])['nodes'] if n['id']=='agentAgentflow_0')
        old=next(n for n in json.loads(before[domain]['flowData'])['nodes'] if n['id']=='agentAgentflow_0')
        assert node['data']['inputs']['agentMessages'][0]['content'].startswith(DRAFT)
        assert node['data']['inputs']['agentModelConfig']==old['data']['inputs']['agentModelConfig']
        assert value['apikeyid']==before[domain]['apikeyid']
    rollback_plan(plan,routes,api,lambda _:None);assert flows==before


def passage(ident,domain='legal'):
    return {'id':ident,'document_id':'law','domain':domain,'domains':[domain],'text':'Основное правило или применимое исключение','section':'Статья 1.'}


def test_forced_and_production_model_evidence_share_exceptions_and_scope_before_and_after():
    rule,exception,private=passage('rule'),passage('exception'),passage('private','private')
    seen=[]
    class Qualifications:
        def augment(self,hits,question,limit):
            seen.append([p['id'] for p in hits]);return hits+[exception,private]
    store=NS(qualifications=Qualifications(),search=lambda *a,**kw:[rule,private])
    forced=prepare_model_evidence('legal','Как действует правило?',store)
    normal=prepare_model_evidence('legal','Как действует правило?',store,[rule,private])
    assert forced==normal==[rule,exception] and seen==[['rule'],['rule']]
    assert prepare_model_evidence('general','question',store,None) is None


def test_drafting_tasks_do_not_confuse_receiving_report_with_disputing_it_or_invent_sources():
    evidence=[{'document_id':'credit-history'}]
    get=' '.join(drafting_focus('Как получить свой кредитный отчет?','',evidence))
    assert 'официальный канал' in get and 'Не добавляй спор' in get
    letter=' '.join(drafting_focus('А напиши заявление об исправлении ошибки.','В кредитном отчете неверные сведения.',evidence))
    assert 'само заявление' in letter and 'заполнители' in letter
    assert drafting_focus('Как получить свой кредитный отчет?','',[])==[]
    assert drafting_focus('Как уйти в отпуск?','',evidence)==[]


def test_criteria_comparison_exports_no_answer_or_expert_approval_and_keeps_forbidden_claims(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    from compare_answer_criteria import compare
    from evaluation_core import digest
    case={'id':'example','domain':'legal','question':'Что регулирует договор?','candidate_reference':'Candidate for human review','required':{'property':'имущественном положении детей'},'forbidden':{'blanket':'безусловно разрешено'},'sources':[]}
    answer='Договор содержит имущественное положение детей.'
    run={'status':'complete','rows':[{'id':'example','case_sha256':digest(case),'answer':answer,'evidence':[]}]}
    proposals={'proposals':{'example':{'original_case_sha256':digest(case),'replace_required':{'property':r'имущественн[а-яё]*\s+положен[а-яё]*\s+дет[а-яё]*'},'remove_required':[]}}}
    result=compare([case],run,proposals)
    assert result['original_passed']==0 and result['proposed_passed']==1 and result['expert_approvals_created']==0
    assert answer not in json.dumps(result,ensure_ascii=False)
    run['rows'][0]['answer']+=' Безусловно разрешено.'
    assert compare([case],run,proposals)['proposed_passed']==0
    changed=copy.deepcopy(case);changed['question']='Другой вопрос'
    with pytest.raises(ValueError,match='changed'):compare([changed],run,proposals)


@pytest.mark.parametrize('phrase',['имущественное положение','**имущественное положение**','имущественное\u00a0положение','_имущественное положение_'])
def test_python_and_actual_flowise_gate_keep_conditions_through_presentation_markup(phrase):
    import shutil,subprocess
    from pathlib import Path
    from agentflow_claims import make_quality_packet,accept_quality_response
    if not shutil.which('node'):pytest.skip('Node unavailable')
    source={'id':'example','text':'Договор определяет имущественное положение детей.','document_id':'example'}
    pair=([{'role':'system','content':'Source data'},{'role':'user','content':'Question'}],[{'role':'system','content':'Review'},{'role':'user','content':'__STS_DRAFT_TO_REVIEW__'}])
    packet=make_quality_packet('legal',*pair,{'question':'Что регулирует соглашение?','previous_question':'','evidence':[source]})
    packet['quality']['constraints']=[{'id':'formatting-test','patterns':[r'имущественн[а-яё ]{0,60}дет'],'guidance':'Synthetic formatting check'}]
    answer='Договор определяет '+phrase+' детей. [S1]'
    audit={'answer_unit_ids':'C1','verdict':'approve','checks':[{'unit_id':'C1','status':'supported','proofs':[{'source_id':'S1','quote':source['text']}]}]}
    gate=Path(__file__).resolve().parents[1]/'agents/quality/claim_gate.js'
    script='const $flow='+json.dumps({'state':{'draft':answer,'quality_json':json.dumps(packet['quality'])}})+';const $audit='+json.dumps(audit)+';console.log(JSON.stringify((function(){'+gate.read_text()+'})()));'
    result=json.loads(subprocess.check_output(['node','-e',script],text=True))
    assert result['audit_ok']=='yes'
    assert accept_quality_response(result['response'],packet)==answer
    tampered=json.loads(result['response']);tampered['answer']=answer.replace('Договор','Договор 100')
    with pytest.raises(ValueError):accept_quality_response(json.dumps(tampered),packet)


def test_condition_normalization_keeps_negation_and_only_removes_presentation():
    from agentflow_claims import constraint_text
    assert constraint_text('Не **означает**\n\u00a0единоличное решение')=='Не означает единоличное решение'
    assert 'Не' in constraint_text('Не _означает_ единоличное решение')


def test_bounded_parallel_evaluation_keeps_case_order_and_counts_only_real_attempts(tmp_path,monkeypatch):
    import asyncio
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    import evaluate_golden,offline_knowledge,local_reply,evaluate_helpfulness
    from evaluation_core import digest
    cases=[{'id':'case-'+str(i),'domain':'legal','question':'Question '+str(i),'candidate_reference':'Synthetic reference','sources':[],'required':{},'forbidden':{}} for i in range(4)]
    path=tmp_path/'cases.json';path.write_text(json.dumps({'cases':cases}))
    monkeypatch.setattr(offline_knowledge,'KnowledgeStore',lambda p:NS())
    monkeypatch.setattr(local_reply,'prepare_local_reply',lambda *a,**kw:NS(answer=None,mechanism='requires_model',evidence=[]))
    monkeypatch.setattr(evaluate_golden,'corpus_digest',lambda p:'synthetic')
    active=0;maximum=0;completion=[]
    async def model(domain,question,evidence,previous,mode,case_id,**kw):
        nonlocal active,maximum
        active+=1;maximum=max(maximum,active)
        await asyncio.sleep(.03 if case_id=='case-0' else .005)
        active-=1;completion.append(case_id);return 'Useful synthetic answer.'
    monkeypatch.setattr(evaluate_helpfulness,'live_answer',model)
    args=NS(force_model=False,concurrency=2,live='flowise',cases=path,knowledge=tmp_path,reviews=None,progress=True,output=tmp_path/'result.json')
    assert asyncio.run(evaluate_golden.run(args))==0
    result=json.loads(args.output.read_text())
    assert maximum==2 and completion[0]!='case-0'
    assert [r['id'] for r in result['rows']]==[c['id'] for c in cases]
    assert all(r['model_attempted'] and r['model_response_returned'] for r in result['rows'])
    assert result['status']=='complete' and result['execution_concurrency']==2 and result['summary']['expert_accuracy'] is None
    assert args.output.stat().st_mode&0o777==0o600
