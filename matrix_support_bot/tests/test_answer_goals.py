"""Coverage and provenance must both survive repair; guides never prove law."""
import copy,json,subprocess
from pathlib import Path
import pytest
from response_goal import response_goal,coverage_errors
from explanation_guides import explanation_guides
from claim_validation import make_quality_packet,accept_quality_response,units
from review_result import ReviewDetails,completeness_note

GATE=Path(__file__).parents[1]/'agents/quality/claim_gate.js'
SOURCE={'id':'S1','document_id':'civil-general','text':'Статья 145. Право на собственное изображение. Использование изображения допускается с согласия изображенного лица.'}
ANSWER='Использование изображения допускается с согласия изображенного лица. [S1]\n\nСохраните переписку.\n\nБанк обязан выплатить 999 тенге.'
AUDIT={'answer_unit_ids':'C1','checks':[
 {'unit_id':'C1','status':'supported','proofs':[{'source_id':'S1','quote':SOURCE['text']}],'standalone':True},
 {'unit_id':'C2','status':'advice','proofs':[],'standalone':True},
 {'unit_id':'C3','status':'unsupported','proofs':[],'standalone':True}]}
def gate(answer,audit,goal,final=True):
 quality={'question_type':'situation','question':'Можно ли публиковать фото человека?','previous_question':'','sources':[SOURCE],'constraints':[],'response_goal':goal}
 script='const $flow='+json.dumps({'state':{'draft':answer,'quality_json':json.dumps(quality)}})+'; const $audit='+json.dumps(audit)+';const $final='+json.dumps('yes' if final else 'no')+';console.log(JSON.stringify((()=>{'+GATE.read_text()+'})()));'
 return json.loads(json.loads(subprocess.check_output(['node','-e',script],text=True))['response'])

@pytest.mark.parametrize('q,kind',[
 ('Имею ли право снимать ценники?','permission'),('Разрешено ли снимать улицу?','permission'),
 ('В чём разница между КАСКО и обязательной страховкой?','comparison'),
 ('Это то же самое, что ГЭСВ?','comparison'),('Начальник не платит, как действовать?','situation')])
def test_task_goal_tracks_different_formulations(q,kind):assert response_goal(q)['kind']==kind

def test_followup_keeps_the_task_and_previous_scope():
 assert 'предыдущему' in response_goal('А страховка?', 'Сравни ГЭСВ и номинальную ставку')['instruction']
 assert response_goal('Напиши требование',format_type='document')['kind']=='document'

def test_guides_require_primary_anchor_and_do_not_create_sources():
 assert not explanation_guides('Можно ли публиковать фото?', '', [])
 assert not explanation_guides('Можно ли публиковать фото?', '', [dict(SOURCE,document_id='wrong')])
 guides=explanation_guides('А если выложить?', 'Я фотографирую прохожих', [SOURCE])
 assert guides[0]['primary_anchors'][0]['source_id']=='S1'
 assert guides[0]['status']=='editorial_plan_not_expert_approved'
 assert not explanation_guides('Как вернуть вклад?', '', [SOURCE])

def test_advice_alone_cannot_pass_as_a_permission_explanation():
 a={'answer_unit_ids':'C1','checks':[{'unit_id':'C1','status':'advice','proofs':[],'standalone':True}]}
 out=gate('Сохраните переписку.',a,response_goal('Можно ли публиковать фото?'))
 assert out['status']=='needs_revision' and 'main_question_unanswered' in out['validation_errors']

def test_unsupported_amount_does_not_destroy_supported_answer_and_next_step():
 goal=response_goal('Можно ли публиковать фото?');out=gate(ANSWER,copy.deepcopy(AUDIT),goal)
 assert out['status']=='approved' and out['partial']
 assert '999' not in out['answer'] and 'Сохраните' in out['answer'] and '[S1]' in out['answer']
 assert out['audit']['answer_unit_ids']=='C1'

def test_unrelated_supported_fragment_cannot_replace_the_removed_main_answer():
 a=copy.deepcopy(AUDIT);a['answer_unit_ids']='C3'
 out=gate(ANSWER,a,response_goal('Можно ли публиковать фото?'))
 assert out['status']=='needs_revision'

@pytest.mark.parametrize('ids',['','C99','C1,C1','C1;C2',None])
def test_invalid_or_missing_main_answer_is_not_trusted(ids):
 a=copy.deepcopy(AUDIT);a['answer_unit_ids']=ids
 assert coverage_errors(a,units(ANSWER),require=True)

def test_client_independently_checks_coverage():
 pair=([{'role':'system','content':'role'},{'role':'user','content':'question'}],[])
 from review_packet import MARKER
 pair=(pair[0],[{'role':'system','content':'review'},{'role':'user','content':MARKER}])
 p=make_quality_packet('legal',*pair,{'question':'Можно ли использовать изображение?','evidence':[SOURCE]})
 out=gate(ANSWER,copy.deepcopy(AUDIT),p['quality']['response_goal'])
 assert accept_quality_response(json.dumps(out),p)
 out['audit']['answer_unit_ids']='C77'
 with pytest.raises(ValueError,match='coverage'):accept_quality_response(json.dumps(out),p)

def test_specific_caveat_contains_no_rejected_claim_or_number():
 note=completeness_note(ReviewDetails((),partial=True,omitted_topics=('rule',),gap_subject='image'))
 assert 'использования изображения' in note and 'не считайте' in note
 assert '999' not in note

def test_wrapped_pdf_rule_and_exception_have_one_exact_selectable_quote():
    from claim_validation import source_excerpts,normalized
    text=('Запрещается продавцу, за исключением случаев, предусмотренных\n'
          'законодательством Республики Казахстан, ограничивать права потребителей в отношении применения фото-,\n'
          'видеосъемки.')
    excerpts=source_excerpts(text)
    complete=[q for q in excerpts if 'Запрещается' in q['quote'] and 'видеосъемки.' in q['quote']]
    assert complete and all(normalized(q['quote']) in normalized(text) for q in excerpts)
    assert all(16<=len(q['quote'])<=550 for q in excerpts)
    assert len({q['id'] for q in excerpts})==len(excerpts)

def test_new_span_excerpts_keep_old_ids_and_have_a_bounded_budget():
    from claim_validation import source_excerpts
    text='\n'.join('Начало нормы '+str(i)+'\nи существенное условие.' for i in range(30))
    out=source_excerpts(text)
    assert out[0]['id']=='Q1' and out[0]['quote']=='и существенное условие.'
    assert sum('\n' in q['quote'] for q in out)<=8

def test_store_filming_plan_does_not_substitute_image_publication_rules():
    evidence=[dict(SOURCE,document_id='consumer-law',text='Запрещается ограничивать права потребителей в отношении применения фото-, видеосъемки.')]
    guides=explanation_guides('Можно ли снимать ценники в магазине?', '', evidence)
    assert [g['id'] for g in guides]==['consumer-recording']

def test_new_case_pack_is_compatible_with_existing_evaluator_and_has_both_domains():
    config=json.loads((Path(__file__).parents[2]/'config/answer-goal-evaluation.json').read_text())
    assert {c['domain'] for c in config['cases']}=={'legal','finance'}
    assert len({c['id'] for c in config['cases']})==len(config['cases'])
    assert all(c['required_patterns'] and isinstance(c['previous_questions'],list) for c in config['cases'])


@pytest.mark.parametrize('q',[
    'Снимаю товар для сравнения цен. Продавец запрещает. Имеет ли он право?',
    'Хочу фото для сравнения. Можно ли фотографировать ценники?'])
def test_incidental_comparison_does_not_override_the_permission_question(q):
    assert response_goal(q)['kind']=='permission'

def test_comparison_in_a_story_is_not_a_comparison_task():
    assert response_goal('Я сравнивал цены, продавец запретил снимать товары')['kind']=='situation'
    assert response_goal('Помоги сравнить две ставки кредита')['kind']=='comparison'
