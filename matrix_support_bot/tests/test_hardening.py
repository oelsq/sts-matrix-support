import asyncio
import copy
import json
from pathlib import Path
import pytest












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






@pytest.mark.parametrize('word,value',[
    ('двенадцатилетнего',12),('тринадцатилетнему',13),('четырнадцатилетний',14),
    ('пятнадцатилетними',15),('шестнадцатилетнего',16),('семнадцатилетней',17),('восемнадцатилетних',18)])
def test_written_age_and_digits_have_identical_quantities_in_python_and_javascript(word,value):
    import subprocess
    from claim_validation import quantities
    from local_claim_code import unit_code
    from numeric_grounding import normalize_age_words
    assert quantities(word)==quantities(str(value)+' лет')=={str(float(value))}
    assert normalize_age_words('до '+word+' возраста')=='до '+str(value)+' лет возраста'
    script=unit_code()+'\nprocess.stdout.write(JSON.stringify([...quantities('+json.dumps(word)+')]));'
    assert set(json.loads(subprocess.check_output(['node','-e',script],text=True)))=={str(value)}


@pytest.mark.parametrize('age,accepted',[(16,True),(15,False),(17,False)])
def test_age_equivalence_never_approves_another_age_or_changes_quote(age,accepted):
    import subprocess
    from claim_validation import validate_audit
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
