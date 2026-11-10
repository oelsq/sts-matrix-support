import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace as NS
import pytest
from search_cache import SearchCache
from claim_validation import safe_advice
from local_claim_code import unit_code


def test_concurrent_retrieval_is_computed_once_and_results_do_not_share_mutations():
    cache=SearchCache();started=threading.Event();release=threading.Event();calls=[]
    def compute():
        calls.append(1);started.set();assert release.wait(2)
        return [{'text':'Public source', 'domains':['legal']}]
    with ThreadPoolExecutor(4) as pool:
        jobs=[pool.submit(cache.get,('legal',b'opaque',8),compute) for _ in range(4)]
        assert started.wait(2);release.set();results=[j.result() for j in jobs]
    assert len(calls)==1
    results[0][0]['domains'].append('private')
    assert all(r[0]['domains']==['legal'] for r in results[1:])
    assert cache.get(('legal',b'opaque',8),compute)[0]['domains']==['legal']
    assert cache.metrics()['inflight']==0


def test_retrieval_exceptions_wake_waiters_and_are_never_cached():
    cache=SearchCache();started=threading.Event();release=threading.Event();calls=[]
    def compute():
        calls.append(1)
        if len(calls)==1:
            started.set();assert release.wait(2);raise ValueError('Synthetic retrieval failure')
        return ['public']
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(cache.get,b'opaque',compute);assert started.wait(2)
        second=pool.submit(cache.get,b'opaque',compute);release.set()
        with pytest.raises(ValueError):first.result()
        assert second.result()==['public']
    assert len(calls)==2 and cache.metrics()['inflight']==0


def test_cache_expiry_eviction_and_configuration_boundaries():
    clock=[0.];cache=SearchCache(2,10,clock=lambda:clock[0]);calls=[]
    def compute():calls.append(1);return ['public']
    for k in ['a','b','a','c']:cache.get(k,compute)
    assert len(calls)==3 and list(cache.values)==['a','c']
    clock[0]=10;cache.get('a',compute);assert len(calls)==4
    for key in [('legal',b'h',8,1),('finance',b'h',8,1),('legal',b'h',6,1),('legal',b'h',8,2)]:cache.get(key,compute)
    assert len(calls)==8 and cache.metrics()['entries']==2


def test_knowledge_search_cache_never_stores_plaintext_question_or_shares_domains():
    from offline_knowledge import KnowledgeStore
    store=KnowledgeStore.__new__(KnowledgeStore);store._search_results=SearchCache();store.semantic=None;calls=[]
    def search(domain,q,limit):calls.append((domain,limit));return [{'text':'public','domains':[domain]}]
    store._search_uncached=search
    question='Synthetic private query marker'
    assert store.search('legal',question,8)==store.search('legal',question,8)
    assert store.search('finance',question,8)[0]['domains']==['finance']
    store.search('legal',question,6);store.semantic=object();store.search('legal',question,8)
    assert len(calls)==4 and question not in repr(list(store._search_results.values))


@pytest.mark.parametrize('line,document,expected',[
    ('Приложения: [копия отчёта], [подтверждения]',True,True),
    ('Кредитор: [название банка]',True,True),
    ('Спорная запись: [описание записи]',True,True),
    ('Номер договора: [номер и дата]',True,True),
    ('Приложения: [копия отчёта]',False,False),
    ('Приложения: [документы] банк обязан исправить',True,False),
    ('Кредитор: [обязан выплатить пеню]',True,False),
    ('Кредитор: Банк выдает кредит без проверки',True,False),
    ('Срок: [три дня]',True,False),
    ('Не переходите по ссылке из сообщения.',False,True),
    ('Не открывайте ссылку, поскольку это гарантирует возврат денег.',False,False),
    ('Обеспечьте свою безопасность при непосредственной угрозе.',False,True),
    ('Обеспечьте возврат денег в течение пяти дней.',False,False),
])
def test_exact_template_and_security_advice_python_javascript_parity(line,document,expected):
    assert safe_advice(line,document=document)==expected
    if not shutil.which('node'):pytest.fail('Real JavaScript runtime required for release verification')
    script='const check=new Function("input","document",'+json.dumps(unit_code()+'\nreturn safeAdvice(input,document);')+');process.stdout.write(JSON.stringify(check('+json.dumps(line)+','+json.dumps(document)+')));'
    assert json.loads(subprocess.check_output(['node','-e',script],text=True))==expected


def test_global_delivery_cooldown_does_not_reclaim_frozen_replies_but_allows_independent_preparation(tmp_path):
    from matrix_inbox import InboxStore
    from matrix_delivery import RequestDispatcher
    from matrix_transport import DeliveryDeferred
    async def run():
        now=[100.];s=InboxStore(tmp_path/'inbox.sqlite3','synthetic',clock=lambda:now[0]);calls=[]
        class Sender:
            cooldown_remaining=0
            async def __call__(self,**kw):
                if now[0]<105:
                    self.cooldown_remaining=105-now[0]
                    raise DeliveryDeferred(self.cooldown_remaining,server_rate_limit=True)
                return NS(event_id='$reply-'+kw['tx_id'])
        sender=Sender()
        for ident,user in [('$one','@a:t'),('$two','@b:t'),('$followup','@a:t')]:
            with s.db:s.enqueue('!r:t',NS(body='synthetic',sender=user,event_id=ident,server_timestamp=100001))
        async def handler(room,e):
            calls.append(e.event_id);await d.send(room.room_id,'m.room.message',{'msgtype':'m.text','body':'Frozen'})
        d=RequestDispatcher(s,NS(rooms={}),handler,sender=sender)
        assert await d.process_next() and await d.process_next()
        assert calls==['$one','$two']
        now[0]=102
        assert not await d.process_next()
        assert s.db.execute('SELECT sum(attempts) FROM inbox_requests').fetchone()[0]==2
        now[0]=105;sender.cooldown_remaining=0
        while await d.process_next():pass
        assert calls==['$one','$two','$followup'] and s.pending()==0
        metrics=s.db.execute('SELECT sum(delivery_attempts),sum(delivery_deferrals),sum(delivery_rate_limits) FROM inbox_requests').fetchone()
        assert tuple(metrics)==(5,2,2)
        s.close()
        s=InboxStore(tmp_path/'inbox.sqlite3','synthetic',clock=lambda:now[0])
        assert s.db.execute('SELECT sum(delivery_rate_limits) FROM inbox_requests').fetchone()[0]==2
        assert not s.db.execute("SELECT 1 FROM inbox_requests WHERE body!=''").fetchone()
        s.close()
    asyncio.run(run())


def test_legacy_inbox_gains_metrics_without_losing_frozen_answer(tmp_path):
    from matrix_inbox import InboxStore
    from types import SimpleNamespace
    import sqlite3
    path=tmp_path/'inbox.sqlite3';s=InboxStore(path,'synthetic',clock=lambda:100)
    with s.db:s.enqueue('!r:t',NS(body='q',sender='@u:t',event_id='$q',server_timestamp=100001))
    s.prepare_reply('$q','final',{'body':'Frozen'},True);s.close()
    db=sqlite3.connect(path)
    for name in ['delivery_attempts','delivery_deferrals','delivery_rate_limits']:db.execute('ALTER TABLE inbox_requests DROP COLUMN '+name)
    db.commit();db.close()
    s=InboxStore(path,'synthetic',clock=lambda:101)
    assert s.final_ready('$q') and json.loads(s.replies('$q')[0]['content'])['body']=='Frozen'
    assert s.db.execute('SELECT delivery_attempts FROM inbox_requests').fetchone()[0]==0
    s.close()


def test_ai_assessment_keeps_rationales_private_and_never_creates_human_approval(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    from review_synthetic_answers import validate_assessment,safe_summary
    value={'findings':[{'code':'missing_condition','severity':'major','answer_unit':None,'source_ids':['S1'],'rationale':'PRIVATE RATIONALE'}],'limitations':'Partial evidence'}
    assert validate_assessment(value,{'A1'},{'S1'})==value
    summary=safe_summary([{'id':'public-case','status':'assessed','assessment':value}])
    assert summary['human_specialist_approvals_created']==0 and summary['finding_counts']=={'missing_condition':1}
    assert 'PRIVATE RATIONALE' not in json.dumps(summary)
    assert 'answer_sha256' not in summary and summary['answers_exported'] is False
    import copy
    for change in ['source','unit','code','empty_source']:
        bad=copy.deepcopy(value)
        if change=='source':bad['findings'][0]['source_ids']=['S999']
        elif change=='unit':bad['findings'][0]['answer_unit']='A999'
        elif change=='empty_source':bad['findings'][0]['source_ids']=[]
        else:bad['findings'][0]['code']='approved_by_lawyer'
        with pytest.raises(ValueError):validate_assessment(bad,{'A1'},{'S1'})


def test_feedback_is_bound_to_own_delivered_answer_replaces_vote_and_contains_no_text(tmp_path):
    from matrix_inbox import InboxStore
    from answer_feedback import feedback_reply
    s=InboxStore(tmp_path/'inbox.sqlite3','synthetic',clock=lambda:100)
    for ident,sender,domain in [('$a','@a:t','legal'),('$b','@b:t','finance')]:
        with s.db:s.enqueue('!r:t',NS(body='SENSITIVE QUESTION',sender=sender,event_id=ident,server_timestamp=100001))
        s.reply_domain(ident,[domain]);s.prepare_reply(ident,'final',{'body':'SENSITIVE ANSWER'},True)
        s.sent(ident,'final','$reply'+ident);s.finish(ident)
    assert 'Пока нет' in feedback_reply(s,'!r:t','@c:t','!оценка полезно')
    assert 'Пока нет' in feedback_reply(s,'!other:t','@a:t','!оценка полезно')
    assert 'сохранена' in feedback_reply(s,'!r:t','@a:t','!оценка полезно')
    assert 'один раз' in feedback_reply(s,'!r:t','@a:t','!оценка полезно')
    assert 'заменена' in feedback_reply(s,'!r:t','@a:t','!оценка ошибка')
    feedback_reply(s,'!r:t','@b:t','!оценка не помогло')
    metrics=s.feedback_metrics()
    assert metrics['by_domain']['legal']=={'suspected_error':1}
    assert metrics['by_domain']['finance']=={'unhelpful':1}
    data=json.dumps([tuple(r) for r in s.db.execute('SELECT * FROM answer_feedback')])
    assert all(v not in data for v in ['SENSITIVE','@a:t','@b:t','!r:t','$a','$b'])
    assert len(s.db.execute('SELECT * FROM answer_feedback').fetchall())==2
    s.close()


def test_feedback_never_rates_pending_reply_or_help_command_and_survives_restart(tmp_path):
    from matrix_inbox import InboxStore
    from answer_feedback import feedback_reply
    p=tmp_path/'inbox.sqlite3';s=InboxStore(p,'synthetic',clock=lambda:100)
    with s.db:s.enqueue('!r:t',NS(body='q',sender='@a:t',event_id='$q',server_timestamp=100001))
    s.reply_domain('$q',['legal','finance']);s.prepare_reply('$q','final',{'body':'a'},True)
    assert s.rate_latest_answer('!r:t','@a:t','helpful')=='no_answer'
    s.sent('$q','final','$answer');s.finish('$q')
    with s.db:s.enqueue('!r:t',NS(body='!помощь',sender='@a:t',event_id='$help',server_timestamp=100001))
    s.prepare_reply('$help','final',{'body':'help'},True);s.sent('$help','final','$helpanswer');s.finish('$help')
    assert feedback_reply(s,'!r:t','@a:t','Обычный вопрос') is None
    assert '!оценка полезно' in feedback_reply(s,'!r:t','@a:t','!оценка пришлю персональные данные')
    assert not s.feedback_metrics()['by_domain']['mixed']
    assert s.rate_latest_answer('!r:t','@a:t','helpful')=='recorded';s.close()
    s=InboxStore(p,'synthetic',clock=lambda:101)
    assert s.rate_latest_answer('!r:t','@a:t','helpful')=='unchanged'
    assert s.feedback_metrics()['by_domain']['mixed']=={'helpful':1};s.close()


def test_matrix_feedback_command_is_local_and_never_calls_model(monkeypatch,tmp_path):
    import bot,time

    from matrix_inbox import InboxStore
    from local_reply import PreparedReply
    output=[];model_preparations=[]
    class Login:pass
    class Matrix:
        user='@bot:test'
        def __init__(self,*args,**kwargs):self.callback=None
        def add_event_callback(self,callback,kind):
            if kind==bot.RoomMessageText:self.callback=callback
        async def login(self,*args,**kwargs):return Login()
        async def room_send(self,**kwargs):output.append(kwargs['content']['body'])
        async def close(self):pass
        async def sync_forever(self,**kwargs):
            for i,(user,text) in enumerate([('@alice:test','Вопрос по праву'),('@bob:test','!оценка полезно'),('@alice:test','!оценка полезно'),('@alice:test','!оценка ошибка'),('@alice:test','!оценка ошибка')]):
                await self.callback(NS(room_id='!r:test'),NS(sender=user,body=text,event_id='$'+str(i),server_timestamp=int(time.time()*1000)+1000))
    def prepare(*args,**kwargs):model_preparations.append(1);return PreparedReply([], 'Конкретный ответ по существу с безопасным следующим действием.', 'synthetic_source')
    for key,value in {'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:test','MATRIX_PASSWORD':'synthetic','LLM_ENDPOINT':'http://litellm:4000/v1','LLM_MODEL':'synthetic','KNOWLEDGE_DIR':'','OFFLINE_MODE':'0','MATRIX_ROOM_ROUTES_FILE':str(tmp_path/'absent'),'MATRIX_INBOX_DB':str(tmp_path/'queue.sqlite3'),'HANDOFF_DB':str(tmp_path/'handoff.sqlite3')}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(bot,'AsyncClient',Matrix);monkeypatch.setattr(bot,'LoginResponse',Login);monkeypatch.setattr(bot,'department_roster',lambda _: {})
    monkeypatch.setattr(bot,'read_llm_key',lambda:'synthetic');monkeypatch.setattr(bot,'prepare_local_reply',prepare)
    asyncio.run(bot.main())
    assert len(model_preparations)==1 and len(output)==5
    assert 'Пока нет' in output[1] and 'сохранена' in output[2] and 'заменена' in output[3] and 'один раз' in output[4]


def test_focused_source_quotes_are_exact_scoped_and_never_invented():
    from focused_sources import focused_sources
    sources=[{'id':'S2','excerpts':[{'id':'Q1','quote':'Договор может определить имущественное положение детей.'}, {'id':'Q2','quote':'Договор не регулирует права и обязанности супругов в отношении детей.'}]}]
    expected=focused_sources(sources,[{'id':'marriage-child-property-vs-parent-duties'}])
    assert len(expected)==2
    for value in expected:
        assert value['source_id']=='S2'
        assert value['quote']==next(q['quote'] for q in sources[0]['excerpts'] if q['id']==value['quote_id'])
    assert focused_sources([], [{'id':'marriage-child-property-vs-parent-duties'}])==[]
    assert focused_sources(sources, [{'id':'unrelated'}])==[]


@pytest.mark.parametrize('answer,expected',[
    ('Не регулируются права и обязанности супругов в отношении детей.',True),
    ('Не регулируются права супругов, а также обязанности супругов в отношении детей.',True),
    ('Права супругов и их обязанности в отношении собственных детей не регулируются.',True),
    ('Не регулируются права супругов в отношении детей.',False),
    ('Не регулируются обязанности супругов в отношении детей.',False),
    ('Права и обязанности определены для супругов.',False),
])
def test_marriage_constraint_keeps_both_concepts_despite_punctuation_or_grammar(answer,expected):
    import re
    from claim_validation import make_quality_packet
    pair=([{'role':'system','content':'Sources'},{'role':'user','content':'Question'}],[{'role':'system','content':'Review'},{'role':'user','content':'__STS_DRAFT_TO_REVIEW__'}])
    evidence=[{'id':'family:41','document_id':'family','text':'имущественное положение детей; права и обязанности супругов в отношении детей'}]
    packet=make_quality_packet('legal',*pair,{'question':'Что регулирует брачный договор?','evidence':evidence})
    condition=next(c for c in packet['quality']['constraints'] if c['id']=='marriage-child-property-vs-parent-duties')
    assert bool(re.search(condition['patterns'][1],answer,re.I))==expected
    script='process.stdout.write(JSON.stringify(new RegExp('+json.dumps(condition['patterns'][1])+',"i").test('+json.dumps(answer)+')));'
    assert json.loads(subprocess.check_output(['node','-e',script],text=True))==expected
    assert 'S1' in packet['draft_user'] and 'Q1' in packet['draft_user']


def test_safe_reviewed_letter_needs_no_invented_legal_citation_but_facts_still_do():
    from agents import render
    from review_result import ReviewDetails
    review=ReviewDetails((),False,'document')
    letter='Кому: [кредитное бюро]\nОт: [ФИО]\nЗаявление\nПрошу проверить запись [описание] и сообщить результат проверки.\nПриложения: [подтверждения]\nДата: [дата]\nПодпись: [подпись]'
    result=render('finance',letter,'Напиши заявление об исправлении записи.',[],review=review)
    assert 'Не удалось' not in result and 'Прошу проверить' in result
    with pytest.raises(ValueError,match='factual assertion'):
        render('finance',letter+'\nБанк обязан немедленно удалить всю историю.', 'Напиши заявление.', [],review=review)
    with pytest.raises(ValueError,match='factual assertion'):
        render('finance',letter+'\nПрошу начислить пеню в течение трёх дней.', 'Напиши заявление.', [],review=review)


@pytest.mark.parametrize('date_text,unsupported',[('09.10.2026',False),('9 октября 2026 года',False),('08.10.2026',True),('24.10.2026',True)])
def test_effective_date_keeps_introduction_separate_from_decision_and_verification_dates(date_text,unsupported):
    from agents import unsupported_quantities
    evidence=[{'id':'tariff','text':'Оплата услуги 0 тенге.', 'version':'Изменения по решению от 08.10.2026, введены с 09.10.2026; файл проверен 24.10.2026', 'reviewed_on':'2026-10-24'}]
    assert unsupported_quantities('Условие действует с '+date_text+'.','Какая комиссия?',evidence,{'tariff'})==unsupported
    evidence[0]['version']='2026-10-09'
    assert unsupported_quantities('Условие действует с 09.10.2026.','Какая комиссия?',evidence,{'tariff'})


def test_feedback_report_reads_only_category_totals_and_cleanup_honors_retention(tmp_path,monkeypatch):
    from matrix_inbox import InboxStore
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    from feedback_metrics import report
    now=1800000000;p=tmp_path/'queue.sqlite3';s=InboxStore(p,'synthetic',clock=lambda:now)
    with s.db:
        s.db.execute('INSERT INTO answer_feedback VALUES(?,?,?,?)',('opaque','legal','helpful','2027-01-15'))
        s.db.execute('INSERT INTO answer_feedback VALUES(?,?,?,?)',('older','legal','suspected_error','2020-01-01'))
    s.close()
    s=InboxStore(p,'synthetic',clock=lambda:now)
    assert s.db.execute('SELECT count(*) FROM answer_feedback').fetchone()[0]==1
    result=report(p,now=now)
    assert result['by_domain']=={'legal':{'helpful':1}}
    assert 'opaque' not in json.dumps(result) and 'older' not in json.dumps(result)
    s.close()


@pytest.mark.parametrize('marker,accepted',[
    ('Кредитное бюро: наименование и адрес',True),
    ('Организация: название бюро',True),
    ('Спорная запись: описание ошибки',True),
    ('Укажите: перечень приложений',True),
    ('law:p1:99',False),
    ('S99',False),
    ('Организация: http://unverified.example',False),
])
def test_reviewed_letter_placeholders_do_not_hide_unknown_sources_or_invented_contacts(marker,accepted):
    from agents import render
    from review_result import ReviewDetails
    source={'id':'law:1','document_id':'law','text':'Проверка проводится при наличии обращения.', 'title':'Synthetic source','reviewed_on':'2026-10-25','source':'https://example.org/source'}
    letter='Проверка проводится при наличии обращения. [S1]\n\nКому: ['+marker+']\nОт: [ФИО]\nПрошу проверить сведения и сообщить результат.\nДата: [дата]\nПодпись: [подпись]'
    review=ReviewDetails((('S1',source['text']),),False,'document')
    result=render('finance',letter,'Напиши заявление.',[source],review=review)
    assert ('Не удалось подготовить ответ с проверяемой' not in result)==accepted


@pytest.mark.parametrize('question,expected',[
    ('Дочь хочет жить со мной, мать против. Кто решает спор?',True),
    ('Мы развелись. Сын выбирает с кем жить. Как решить спор?',True),
    ('Дочь выбирает профессию и живёт со мной.',False),
    ('Сын живёт со мной, кто платит его кредит?',False),
    ('Дочери 23 года, она спорит с кем жить.',False),
    ('Совершеннолетняя дочь спорит с кем жить.',False),
])
def test_kinship_paraphrases_preserve_residence_scope_and_do_not_assume_adult_child_is_minor(question,expected):
    from retrieval_intents import child_residence_question
    assert child_residence_question(question)==expected


def test_unknown_insurance_type_gets_concrete_request_without_transplanting_auto_rule():
    from response_guidance import request_clarification
    question='Страховая вычла из выплаты за аварию свою оплату определения ущерба. Что запросить?'
    answer=request_clarification('finance',question)
    assert 'расчёт' in answer and 'расходы' in answer and 'вид страхования' in answer
    assert '!человек' not in answer and 'Статья' not in answer and 'нельзя удерживать' not in answer
    assert request_clarification('finance','После ДТП страховщик удержал расходы на оценку. Это допустимо?') is None
    assert request_clarification('finance','Как вычесть страховку при расчёте ГЭСВ?') is None


def test_marriage_contact_restriction_requires_actual_source_and_is_not_property_rule():
    from checked_answers import checked_legal_answer
    q='Хочу через брачный договор запретить супругу видеться с ребёнком. Так можно?'
    p={'id':'family:41:1','document_id':'family','section':'Статья 41.','title':'Synthetic family clause','reviewed_on':'2026-10-25','source':'https://example.org/law','text':'Брачный договор не может регулировать права и обязанности супругов в отношении детей.'}
    answer=checked_legal_answer(q,[p])
    assert 'права и обязанности' in answer and 'запрет общения' in answer
    assert checked_legal_answer(q,[]) is None
    assert checked_legal_answer('Хочу через брачный договор запретить сыну тратить подаренные деньги.',[p]) is None


def test_marriage_property_qualification_is_for_definition_not_any_parental_situation():
    from claim_validation import make_quality_packet
    pair=([{'role':'system','content':'Sources'},{'role':'user','content':'Question'}],[{'role':'system','content':'Review'},{'role':'user','content':'__STS_DRAFT_TO_REVIEW__'}])
    evidence=[{'id':'family:41','document_id':'family','text':'имущественное положение детей; права и обязанности супругов в отношении детей'}]
    packet=make_quality_packet('legal',*pair,{'question':'Хочу через брачный договор запретить супругу видеться с ребёнком. Так можно?','evidence':evidence})
    assert not any(c['id']=='marriage-child-property-vs-parent-duties' for c in packet['quality']['constraints'])
