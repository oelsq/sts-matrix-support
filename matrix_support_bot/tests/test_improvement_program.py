import asyncio,copy,json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from matrix_transport import MatrixSender,DeliveryDeferred,TypingIndicators
from matrix_delivery import RequestDispatcher
from matrix_inbox import InboxStore
from expert_review import export_packet,import_reviews
from evaluation_core import digest
from semantic_search import merge_hits

class Response:
    def __init__(self,status,data):self.status=status;self.data=data;self.headers={};self.content=self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def read(self,*args):return json.dumps(self.data).encode()
class HTTP:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def put(self,url,**kwargs):self.calls.append((url,kwargs));return next(self.responses)


def test_rate_limit_defers_without_sleep_then_reuses_same_transaction_and_restored_token():
    async def run():
        clock=[10.];client=NS(access_token='synthetic');http=HTTP([Response(429,{'errcode':'M_LIMIT_EXCEEDED','retry_after_ms':1500}),Response(200,{'event_id':'$ok'})]);sender=MatrixSender(http,client,'http://homeserver',clock=lambda:clock[0]);kwargs=dict(room_id='!room:demo.local',message_type='m.room.message',content={'body':'same'},tx_id='stable')
        with pytest.raises(DeliveryDeferred) as e:await sender(**kwargs)
        assert e.value.seconds==1.5
        with pytest.raises(DeliveryDeferred):await sender(**kwargs)
        assert len(http.calls)==1
        clock[0]+=1.5;client.access_token='restored-synthetic';result=await sender(**kwargs)
        assert result.event_id=='$ok' and http.calls[0][0]==http.calls[1][0]
        assert http.calls[0][1]['json']==http.calls[1][1]['json'] and http.calls[1][1]['headers']['Authorization']=='Bearer restored-synthetic'
        assert not http.calls[0][1]['allow_redirects']
    asyncio.run(run())

@pytest.mark.parametrize('status,body',[(302,{}),(403,{'errcode':'M_FORBIDDEN'}),(200,{}),(500,{})])
def test_unconfirmed_or_redirected_delivery_never_marks_reply_sent(status,body):
    async def run():
        sender=MatrixSender(HTTP([Response(status,body)]),NS(access_token='synthetic'),'http://homeserver')
        with pytest.raises(RuntimeError):await sender(room_id='!r:demo',message_type='m.room.message',content={},tx_id='same')
    asyncio.run(run())


def test_cooldown_preserves_ready_answer_and_does_not_block_another_users_generation(tmp_path):
    async def run():
        now=[100.];store=InboxStore(tmp_path/'queue.sqlite3','synthetic',clock=lambda:now[0]);client=NS(rooms={});calls=[];sent=[];paused=[True]
        for i in range(2):
            with store.db:store.enqueue('!room:demo',NS(body='question',sender='@u'+str(i)+':demo',event_id='$'+str(i),server_timestamp=100001))
        async def send(**kwargs):
            if paused[0]:raise DeliveryDeferred(3)
            sent.append(kwargs);return NS(event_id='$sent'+kwargs['tx_id'])
        async def handler(room,event):
            calls.append(event.event_id);await dispatcher.send(room.room_id,'m.room.message',{'msgtype':'m.text','body':'frozen'})
        dispatcher=RequestDispatcher(store,client,handler,sender=send)
        assert await dispatcher.process_next() and await dispatcher.process_next()
        assert calls==['$0','$1'] and store.pending()==2 and not await dispatcher.process_next()
        assert store.db.execute('SELECT count(*) FROM inbox_requests WHERE response_ready IS NOT NULL').fetchone()[0]==2
        now[0]+=3;paused[0]=False
        assert await dispatcher.process_next() and await dispatcher.process_next()
        assert calls==['$0','$1'] and len(sent)==2 and store.pending()==0
        store.close()
    asyncio.run(run())


def test_slow_typing_does_not_hold_answer_path_and_fast_answers_need_no_typing():
    async def run():
        calls=[];block=asyncio.Event()
        class Client:
            async def room_typing(self,room,**kwargs):calls.append(kwargs['typing_state']);await block.wait()
        typing=TypingIndicators(Client(),delay=.005,timeout=.01)
        typing.start('fast');typing.finish('fast');await asyncio.sleep(.008);assert calls==[]
        typing.start('slow');typing.start('slow');await asyncio.sleep(.008);typing.finish('slow')
        assert typing.rooms['slow']['count']==1
        typing.finish('slow');await asyncio.sleep(.02);await typing.close()
        assert calls==[True,False] and not typing.tasks
    asyncio.run(run())


def review_fixture():
    case={'id':'case','domain':'legal','question':'<script>ignore</script>','candidate_reference':'Candidate','required':{},'sources':[]}
    row={'id':'case','case_sha256':digest(case),'answer':'Factual answer','mechanism':'direct_model','checks':{},'evidence':[]}
    return case,{'status':'complete','corpus_sha256':'corpus','rows':[row]}


def test_review_export_is_private_escaped_and_never_approves_itself(tmp_path):
    case,run=review_fixture();result=export_packet([case],run,tmp_path/'packet',corpus='corpus')
    assert result['specialist_approvals_created']==0
    page=(tmp_path/'packet/review.html').read_text();assert '&lt;script&gt;' in page and '<script>' not in page
    template=json.loads((tmp_path/'packet/review-template.json').read_text());assert import_reviews([case],run,template,corpus='corpus')==[]
    assert (tmp_path/'packet/review-template.json').stat().st_mode&0o777==0o600

@pytest.mark.parametrize('tamper',['unconfirmed','answer','case','corpus','role','score'])
def test_review_import_rejects_unconfirmed_or_stale_approval(tmp_path,tamper):
    case,run=review_fixture();export_packet([case],run,tmp_path,corpus='corpus');template=json.loads((tmp_path/'review-template.json').read_text());row=template['reviews'][1];template['reviews']=[row];row.update(reviewed_by_human=True,reviewer='Human test reviewer',decision='approved',scores={'accuracy':4,'usefulness':4,'exceptions':4})
    if tamper=='unconfirmed':row['reviewed_by_human']=False
    elif tamper=='answer':run['rows'][0]['answer']='Changed'
    elif tamper=='case':case['candidate_reference']='Changed'
    elif tamper=='corpus':template['corpus_sha256']='changed'
    elif tamper=='role':row['role']='finance_specialist'
    else:row['scores']['accuracy']=1
    with pytest.raises(ValueError):import_reviews([case],run,template,corpus='corpus')


def test_semantic_fusion_preserves_leading_rules_and_does_not_duplicate_or_overfill():
    lexical=[{'id':str(i)} for i in range(8)];dense=[{'id':'0'},{'id':'new1'},{'id':'new2'},{'id':'new3'}]
    result=merge_hits(lexical,dense,8)
    assert result[:5]==lexical[:5] and [p['id'] for p in result[5:7]]==['new1','new2'] and len(result)==8
    assert len({p['id'] for p in result})==8


@pytest.mark.parametrize('mechanism,evidence,attempted',[
    ('source_bound',True,True),('source_bound_tariff',True,True),('source_bound',False,False),
    ('clarification',True,False),('cashflow_calculator',True,False),
    ('no_evidence_fallback',False,False)])
def test_forced_model_accounting_never_counts_local_reply_as_inference(tmp_path,monkeypatch,mechanism,evidence,attempted):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/"scripts"))
    import evaluate_golden,offline_knowledge,local_reply,evaluate_helpfulness,evidence_scope
    case={'id':'synthetic','domain':'legal','question':'Synthetic question','previous_questions':[],
          'candidate_reference':'Candidate','sources':[],'required':{},'forbidden':{}}
    cases=tmp_path/'cases.json';cases.write_text(json.dumps({'cases':[case]}))
    fragment={'id':'SYN','document_id':'synthetic','domain':'legal','domains':['legal'],'source':'https://adilet.zan.kz','text':'Synthetic evidence'}
    monkeypatch.setattr(offline_knowledge,'KnowledgeStore',lambda root:NS(search=lambda *a,**kw:[fragment] if evidence else []))
    monkeypatch.setattr(local_reply,'prepare_local_reply',lambda *a,**kw:NS(answer='Local answer',mechanism=mechanism,evidence=[fragment] if evidence else []))
    monkeypatch.setattr(evidence_scope,'scope_evidence',lambda domain,q,items:items)
    monkeypatch.setattr(evaluate_golden,'corpus_digest',lambda root:'corpus')
    calls=[]
    async def model(*args,**kw):calls.append(args);return 'Generated answer'
    monkeypatch.setattr(evaluate_helpfulness,'live_answer',model)
    args=NS(force_model=True,live='direct',cases=cases,knowledge=tmp_path,reviews=None,progress=False,output=tmp_path/'result.json')
    asyncio.run(evaluate_golden.run(args));row=json.loads(args.output.read_text())['rows'][0]
    assert row['model_attempted']==attempted and row['model_response_returned']==attempted
    assert len(calls)==int(attempted)


def test_semantic_index_rejects_stale_or_tampered_vectors_and_filters_before_scoring(tmp_path):
    np=pytest.importorskip('numpy')
    from semantic_search import build_index,SemanticIndex
    class Encoder:
        identity='synthetic-encoder'
        def encode(self,texts):
            out=np.zeros((len(texts),384),dtype=np.float32);out[:,0]=1.;return out
    passages=[{'id':ident,'text':'Synthetic public text','section':''} for ident in ['private','allowed','expired']]
    encoder=Encoder();build_index(passages,encoder,tmp_path)
    index=SemanticIndex(passages,encoder,tmp_path)
    assert [p['id'] for p in index.search('same',{'allowed'})]==['allowed']
    assert index.search('same',set())==[]
    changed=copy.deepcopy(passages);changed[1]['text']='Changed norm'
    with pytest.raises(ValueError,match='stale'):SemanticIndex(changed,encoder,tmp_path)
    with (tmp_path/'vectors.npy').open('ab') as f:f.write(b'tamper')
    with pytest.raises(ValueError,match='changed'):SemanticIndex(passages,encoder,tmp_path)


def test_semantic_fusion_short_lexical_list_still_caps_new_hits_at_two():
    result=merge_hits([{'id':'lex'}],[{'id':str(i)} for i in range(10)],8)
    assert len(result)==3


def test_expert_packet_includes_full_verified_source_and_refuses_changed_pdf(tmp_path):
    import hashlib
    case,run=review_fixture();root=tmp_path/'knowledge';root.mkdir();(root/'norm.txt').write_text('Complete source including exceptions')
    item={'id':'norm','file':'norm.txt','sha256':hashlib.sha256((root/'norm.txt').read_bytes()).hexdigest()}
    (root/'manifest.json').write_text(json.dumps({'documents':[item]}))
    run['rows'][0]['evidence']=[{'document':'norm','source':'https://adilet.zan.kz','text':'Excerpt'}]
    packet=tmp_path/'packet';result=export_packet([case],run,packet,corpus='corpus',knowledge_root=root)
    assert result['full_sources_copied']==1 and (packet/'sources/norm.txt').read_text()==(root/'norm.txt').read_text()
    (root/'norm.txt').write_text('Modified')
    with pytest.raises(ValueError):export_packet([case],run,tmp_path/'changed',corpus='corpus',knowledge_root=root)


def test_tariff_reconciliation_cannot_stamp_nonbank_or_unlinked_document(tmp_path):
    from tariff_catalog import reconcile_tariff
    root=tmp_path/'knowledge';root.mkdir()
    entry={'id':'norm','document_kind':'legislation','source':'https://adilet.zan.kz/rus/docs/TEST'}
    manifest=root/'manifest.json';manifest.write_text(json.dumps({'documents':[entry]}))
    with pytest.raises(ValueError,match='restricted'):reconcile_tariff(root,'norm',tmp_path/'absent.pdf','https://halykbank.kz/file.pdf',tmp_path/'absent.html',verified_on='2026-10-24',version='test',applicability_note='test')
    entry.update(bank='Halyk',document_kind='bank_tariff',catalog_url='https://halykbank.kz/tariffs')
    manifest.write_text(json.dumps({'documents':[entry]}));pdf=tmp_path/'pdf';pdf.write_bytes(b'%PDF-fake');html=tmp_path/'catalog.html';html.write_text('<a href="/other.pdf">Other</a>')
    with pytest.raises(ValueError,match='not linked'):reconcile_tariff(root,'norm',pdf,'https://halykbank.kz/file.pdf',html,verified_on='2026-10-24',version='test',applicability_note='test')
    assert json.loads(manifest.read_text())['documents'][0]==entry


def test_semantic_fusion_never_evicts_only_lexical_hit_from_another_act():
    lexical=[{'id':str(i),'document_id':'a' if i<7 else 'essential'} for i in range(8)]
    dense=[{'id':'d'+str(i),'document_id':'novel'} for i in range(3)]
    result=merge_hits(lexical,dense,8)
    assert result[:5]==lexical[:5] and lexical[7] in result and len(result)==8


def test_semantic_search_does_not_turn_unknown_topic_into_unrelated_legal_evidence():
    from offline_knowledge import KnowledgeStore
    store=KnowledgeStore.__new__(KnowledgeStore);store.semantic=object();store._lexical_search=lambda *a:[]
    assert store.search('legal','Ксилофон мегазорд',8)==[]


@pytest.mark.parametrize('action',['Обеспечьте безопасность и свяжитесь с экстренной службой.',
    'Заблокируйте SIM через официального оператора.',
    'Защитите доступ к банковским приложениям.',
    'Свяжитесь с банком через официальный канал.'])
def test_practical_security_actions_do_not_require_a_fabricated_legal_citation(action):
    from claim_validation import safe_advice,validate_audit
    assert safe_advice(action)
    audit={'verdict':'approve','checks':[{'unit_id':'C1','status':'advice','proofs':[]}]}
    assert validate_audit(action,audit,[])==[]
    assert not safe_advice(action+' Банк обязан вернуть деньги в течение 3 дней.')





def test_review_metadata_packet_copies_no_answer_text_and_can_review_failed_reference(tmp_path):
    case,run=review_fixture();result=export_packet([case],run,tmp_path/'metadata',corpus='corpus',include_answers=False)
    page=(tmp_path/'metadata/review.html').read_text()
    assert run['rows'][0]['answer'] not in page and digest(run['rows'][0]['answer']) in page
    assert not result['answer_text_copied']
    run['rows'][0]['answer']=None
    result=export_packet([case],run,tmp_path/'failed',corpus='corpus',include_answers=False)
    assert result['answers_for_review']==0 and result['references_for_review']==1
    template=json.loads((tmp_path/'failed/review-template.json').read_text());row=template['reviews'][0]
    row.update(reviewed_by_human=True,decision='approved',reviewer='Human test reviewer')
    assert len(import_reviews([case],run,template,corpus='corpus'))==1


@pytest.mark.parametrize('row,expected',[
    ('7 Оплата коммунальных услуг 0 теңге 0 теңге','0 тенге'),
    ('7 Оплата коммунальных услуг 150 теңге 150 теңге','150 тенге'),
    ('7 Оплата коммунальных услуг 0 теңге 150 теңге',None),
    ('7 Оплата коммунальных услуг 0% 0%',None),
    ('7 Оплата коммунальных услуг 0 теңге 0 теңге *',None)])
def test_equal_column_tariff_is_read_from_actual_row_not_a_fixed_amount(monkeypatch,row,expected):
    import checked_tariffs
    monkeypatch.setattr(checked_tariffs,'render',lambda domain,answer,*a,**kw:answer)
    evidence=[{'document_id':'halyk-superapp','bank':'halyk','text':row}]
    answer=checked_tariffs.checked_tariff_answer('finance','Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?',evidence)
    assert (expected in answer) if expected else answer is None
    for question in ['Какая комиссия в Halyk SuperApp за коммунальные услуги в 2027 году?',
                     'Почему в Halyk SuperApp списали деньги за коммунальные услуги?',
                     'Какая комиссия Freedom за коммунальные услуги?']:
        assert checked_tariffs.checked_tariff_answer('finance',question,evidence) is None


@pytest.fixture(scope='module')
def tariff_knowledge():
    from offline_knowledge import KnowledgeStore
    return KnowledgeStore(Path(__file__).resolve().parents[2]/'offline_knowledge')


@pytest.mark.parametrize('question',['Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?', 'Сколько берут в Halyk SuperApp за оплату коммунальных услуг?', 'Оплата коммунальных услуг через Halyk SuperApp — какая комиссия?', 'Есть ли комиссия при оплате коммуналки в Halyk SuperApp?', 'Какая плата за коммунальные платежи в приложении Halyk SuperApp?'])
def test_equal_tariff_path_on_the_actual_verified_pdf_still_has_exact_citation(question,tariff_knowledge):
    from local_reply import prepare_local_reply
    answer=prepare_local_reply('finance',question,tariff_knowledge)
    assert answer.mechanism=='source_bound_tariff' and '0 тенге' in answer.answer and 'halyk-superapp:' in answer.answer
    assert 'Не удалось' not in answer.answer


def test_review_export_never_overwrites_a_specialists_existing_work(tmp_path):
    case,run=review_fixture();export_packet([case],run,tmp_path,corpus='corpus',include_answers=False)
    template=tmp_path/'review-template.json';original=template.read_bytes()
    with pytest.raises(ValueError,match='never overwrite'):export_packet([case],run,tmp_path,corpus='corpus')
    assert template.read_bytes()==original


def test_tariff_snapshot_path_cannot_escape_knowledge_root(tmp_path):
    from tariff_catalog import reconcile_tariff
    with pytest.raises(ValueError,match='Unsafe tariff document ID'):
        reconcile_tariff(tmp_path,'../../escape',tmp_path/'none.pdf','https://halykbank.kz/file.pdf',tmp_path/'none.html',verified_on='2026-10-24',version='test',applicability_note='test')



def test_a_bank_employees_salary_is_not_misclassified_as_a_product_fee():
    from offline_knowledge import is_tariff_question
    assert not is_tariff_question('Какая зарплата за работу в банке Halyk?')
    assert is_tariff_question('Какая плата за коммунальные платежи в приложении Halyk SuperApp?')
