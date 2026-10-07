"""Corpus import, temporal isolation and broad retrieval regressions."""
import hashlib,json,os
from pathlib import Path
import pytest
from offline_knowledge import KnowledgeStore,KnowledgeError
from official_sources import parse_official_page,fetch_one,SourceError
from agents import render,route,unsupported_quantities
from message_format import matrix_content
from knowledge_coverage import coverage_answer

@pytest.fixture(scope='module')
def corpus():
    return KnowledgeStore(os.getenv('TEST_KNOWLEDGE_DIR',str(Path(__file__).resolve().parents[2]/'offline_knowledge')))

@pytest.mark.parametrize('domain,q,doc,article',[
 ('legal','Как подать на алименты?','family','Статья 139.'),
 ('legal','Развелись, с кем будет жить ребенок?','family','Статья 73.'),
 ('legal','Как подать иск в гражданский суд?','civil-procedure','Статья 148.'),
 ('legal','Меня задержали, могу ли потребовать адвоката?','criminal-procedure','Статья 64.'),
 ('finance','Какая ставка НДС в Казахстане в 2026 году?','tax','Статья 503.'),
 ('legal','Какие права есть у пациента?','health','Статья 134.'),
 ('legal','Как пожаловаться на отказ акимата?','administrative-procedure','Статья 92.'),
 ('legal','Какие выплаты бывают при потере работы?','social','Статья 113.'),
 ('legal','Какие обязанности у государственного служащего?','state-service','Статья 17.'),
])
def test_new_branches_find_primary_rule(corpus,domain,q,doc,article):
    found=corpus.search(domain,q,8)
    assert any(p['document_id']==doc and str(p['section']).startswith(article) for p in found[:5])


def test_supreme_court_text_has_point_locators(corpus):
    found=[p for p in corpus.passages if p['document_id']=='court-labor']
    assert any(str(p['section']).startswith('Пункт ') for p in found)
    assert all(p['document_kind']=='supreme_court_resolution' for p in found)


def test_current_tax_and_state_service_exclude_known_future_norms(corpus):
    found=corpus.search('finance','Налоговое администрирование статья 92 глава 90',10000)
    assert not any(p['document_id']=='tax' and str(p['section']).startswith(('Статья 92.','Статья 790.')) for p in found)
    found=corpus.search('legal','Подготовка к поступлению на государственную службу',10000)
    assert not any(p['document_id']=='state-service' and str(p['section']).startswith(('Статья 18.','Статья 19.','Статья 20.')) for p in found)
    assert not any('Z1500000416' in p['source'] for p in corpus.passages)


def test_postings_and_cached_statistics_preserve_results(corpus):
    q='Как подать на алименты?'
    one=corpus.search('legal',q,8);two=corpus.search('legal',q,8)
    assert [p['id'] for p in one]==[p['id'] for p in two]
    assert corpus._candidate_cache


def test_child_residence_includes_right_to_express_opinion(corpus):
    found=corpus.search('legal','Развелись, с кем будет жить ребёнок?',8)
    assert any(p['document_id']=='family' and str(p['section']).startswith('Статья 62.') for p in found)
    assert any(p['document_id']=='family' and str(p['section']).startswith('Статья 73.') for p in found)


def test_text_integrity_checked_when_declared(tmp_path):
    docs=[]
    for domain in ['legal','finance']:
        text='Статья 1. Пример\nПроверяемый текст.';file=tmp_path/(domain+'.txt');file.write_text(text)
        docs.append({'id':domain,'domain':domain,'title':domain,'reviewed_on':'2026-10-07','source':'official',
                     'file':file.name,'artifact_kind':'official_text_snapshot','sha256':hashlib.sha256(text.encode()).hexdigest()})
    (tmp_path/'manifest.json').write_text(json.dumps({'documents':docs}))
    KnowledgeStore(tmp_path)
    (tmp_path/'legal.txt').write_text('Подменённый текст')
    with pytest.raises(KnowledgeError,match='SHA-256'):KnowledgeStore(tmp_path)


def fixture_html(status='Обновленный',body=None):
    body=body or 'Статья 1. Норма\n'+('Текст нормы. '*180)+'\nСтатья 2. Другая норма'
    return '<h1>Закон о проверке</h1><p>'+status+'</p><article><p>'+body.replace('\n','</p><p>')+'</p><script>ignore()</script></article>'


def test_import_rejects_repealed_shell_and_wrong_document():
    with pytest.raises(SourceError,match='Repealed'):parse_official_page(fixture_html('Утративший силу'))
    with pytest.raises(SourceError,match='title'):parse_official_page('<h1>Ошибка</h1>')
    with pytest.raises(SourceError,match='Unexpected'):parse_official_page(fixture_html(),expected_title='Совершенно другой закон')
    assert 'ignore' not in parse_official_page(fixture_html())['text']
    assert parse_official_page(fixture_html('Исправление ошибки'))['status']=='Исправление ошибки'


def test_import_cannot_use_arbitrary_url_or_destination_name(tmp_path):
    with pytest.raises(SourceError):fetch_one({'id':'../oops','code':'Z1300000094','domain':'legal'},tmp_path)
    with pytest.raises(SourceError):fetch_one({'id':'safe','code':'https://example.com','domain':'legal'},tmp_path)


def test_text_source_card_shows_article_without_inventing_pdf_page():
    evidence=[{'id':'family:5:1','document_id':'family','title':'Семейный кодекс','section':'Статья 139. Алименты',
               'text':'Норма о содержании ребенка.','source':'https://old.adilet.zan.kz/rus/docs/K1100000518','reviewed_on':'2026-10-07'}]
    answer=render('legal','Норма о содержании ребенка. [S1]','',evidence)
    content=matrix_content(answer)
    assert 'Статья 139' in content['formatted_body'] and 'стр.' not in content['formatted_body']


def test_worded_zero_rate_is_not_an_invented_number():
    ev=[{'id':'tax:1:1','text':'Указанные обороты облагаются по нулевой ставке. Общая ставка 16 процентов.'}]
    assert not unsupported_quantities('0% [S1]', '', ev, {'tax:1:1'})
    assert unsupported_quantities('Ставка 17 процентов [S1]', '', ev, {'tax:1:1'})


def test_scope_description_is_not_a_full_coverage_claim(corpus):
    answer=coverage_answer(corpus)
    assert 'Семья' in answer and 'Налоги' in answer and 'не означает' in answer
    assert '!человек' not in answer
    assert 'пока не подключена' in coverage_answer(None)
    assert route('Какая ставка НДС?')==('finance',)
    assert route('Как взыскать алименты?')==('legal',)
