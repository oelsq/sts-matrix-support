import asyncio,copy,json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from nio import RoomSendResponse
from evaluation_core import digest,record_review,current_reviews,summarize,private_json,automated_checks
from evidence_neighbors import QualificationIndex
from knowledge_updates import inventory,check_source,approve_candidate,validate_url
from matrix_inbox import InboxStore
from matrix_delivery import RequestDispatcher

CASE={'id':'q','domain':'legal','question':'Что делать?','candidate_reference':'Сохранить условия нормы','required':{'exception':'кроме'},'sources':['law']}

def test_no_automatic_approval_or_accuracy_from_a_green_regex_check():
    check=automated_checks(CASE,'Кроме предусмотренных случаев, сохраните документы.',[])
    assert check['passed'] and check['expert_accuracy'] is None
    assert summarize([{'answer':'text','checks':check,'reviews':{}}])['expert_accuracy'] is None
    assert summarize([{'answer':None}])['automatic_passed']==0

@pytest.mark.parametrize('changed',['case','corpus','answer'])
def test_review_is_bound_to_case_corpus_and_exact_answer(changed):
    c=copy.deepcopy(CASE);corpus='fingerprint';answer='Exact answer'
    reviews=[record_review(c,corpus,reviewer='Human test reviewer',role='legal_specialist',kind='reference',decision='approved'),record_review(c,corpus,reviewer='Human test reviewer',role='legal_specialist',kind='answer',decision='approved',answer=answer,scores={'accuracy':4,'usefulness':3,'exceptions':4})]
    assert set(current_reviews(c,corpus,answer,reviews))=={'reference','answer'}
    if changed=='case':c['candidate_reference']='Changed rule'
    if changed=='corpus':corpus='new'
    if changed=='answer':answer+=' extra claim'
    result=current_reviews(c,corpus,answer,reviews)
    assert 'answer' not in result


def test_rejected_specialist_answer_is_in_the_scoring_denominator():
    r=record_review(CASE,'x',reviewer='Human test reviewer',role='legal_specialist',kind='answer',decision='rejected',answer='bad',scores={'accuracy':0,'usefulness':1,'exceptions':0})
    result=summarize([{'answer':'bad','checks':{'passed':False},'reviews':{'reference':{'decision':'approved'},'answer':r}}])
    assert result['specialist_scored']==1 and result['expert_accuracy']==0


def test_invalid_role_or_unjustified_approval_is_rejected():
    with pytest.raises(ValueError):record_review(CASE,'x',reviewer='x',role='finance_specialist',kind='reference',decision='approved')
    with pytest.raises(ValueError):record_review(CASE,'x',reviewer='x',role='legal_specialist',kind='answer',decision='approved',answer='x',scores={'accuracy':2,'usefulness':4,'exceptions':4})


def test_review_and_answer_files_are_private_even_if_old_file_was_public(tmp_path):
    p=tmp_path/'review.json';p.write_text('');p.chmod(0o644);private_json(p,{'safe':'data'})
    assert p.stat().st_mode&0o777==0o600


def passage(ident,section,text,doc='law',domains=None):return {'id':ident,'document_id':doc,'section':section,'text':text,'domains':domains or ['legal']}

def test_exception_companions_preserve_primary_results_and_never_cross_acts_or_scope():
    hits=[passage(str(i),'Статья 8.','Основное правило') for i in range(8)]
    same=passage('except','Статья 8.','Отзыв не допускается при наличии обязательства.')
    foreign=passage('foreign','Статья 8.','Кроме случаев','other-law')
    private=passage('private','Статья 8.','Кроме случаев',domains=['private'])
    out=QualificationIndex(hits+[same,foreign,private]).augment(hits,'Отзыв согласия')
    assert out[:3]==hits[:3] and same in out and foreign not in out and private not in out and len(out)==8


def test_qualification_budget_is_bounded_and_does_not_duplicate():
    hit=passage('a','Статья 8.','Основное правило');others=[passage(str(i),'Статья 8.','Кроме случаев') for i in range(10)]
    index=QualificationIndex([hit]+others)
    assert len(index.augment([hit],''))==3
    assert index.augment([hit],'',limit=1)==[hit]
    assert len({p['id'] for p in index.augment([hit,others[0]],'')})==4


def test_offline_freshness_is_not_a_current_law_certificate(tmp_path):
    import hashlib
    from datetime import date
    p=tmp_path/'law.txt';p.write_text('law')
    item={'id':'law','file':'law.txt','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source':'https://adilet.zan.kz/rus/docs/K1500000414','reviewed_on':'2026-09-01'}
    (tmp_path/'manifest.json').write_text(json.dumps({'documents':[item]}))
    row=inventory(tmp_path,today=date(2026,10,23))['documents'][0]
    assert row['status']=='review_due' and row['legal_currency_certified'] is False
    p.write_text('tampered');assert inventory(tmp_path)['documents'][0]['status']=='integrity_error'

@pytest.mark.parametrize('url',['http://adilet.zan.kz/x','https://127.0.0.1/x','https://adilet.zan.kz.evil.example/x','https://user:secret@adilet.zan.kz/x'])
def test_official_monitor_refuses_untrusted_destinations(url):
    with pytest.raises(ValueError):validate_url(url)


def test_javascript_shell_is_not_a_successful_document_update(tmp_path):
    item={'id':'labor','source':'https://adilet.zan.kz/rus/docs/K1500000414','title':'Трудовой кодекс','sha256':'old'}
    result=check_source(item,tmp_path,fetch=lambda _:b'<html>Enable JavaScript</html>')
    assert result['status']=='check_unavailable' and result['automatic_activation'] is False


def test_changed_pdf_stays_staged_and_approval_is_invalidated_by_tampering(tmp_path):
    item={'id':'tariff','source':'https://halykbank.kz/tariff.pdf','sha256':'old'}
    receipt=check_source(item,tmp_path,fetch=lambda _:b'%PDF-1.4 test candidate')
    assert receipt['status']=='candidate_changed' and item['sha256']=='old'
    p=tmp_path/receipt['artifact_file']
    a=approve_candidate(item,receipt,p,reviewer='Human test reviewer',version='Explicit reviewed edition',applicability_note='Reviewed dates and conditions')
    assert a['decision']=='approved_for_promotion'
    p.write_bytes(b'%PDF-tamper')
    with pytest.raises(ValueError):approve_candidate(item,receipt,p,reviewer='Human test reviewer',version='v',applicability_note='note')


def test_two_workers_isolate_request_state_and_preserve_each_users_order(tmp_path):
    async def run():
        s=InboxStore(tmp_path/'inbox.db','test',clock=lambda:100);active=set();maximum=0;order={};sends=[];done=asyncio.Event()
        for i in range(12):
            e=NS(event_id='$'+str(i),sender='@u'+str(i%3),body=str(i),server_timestamp=100_001)
            with s.db:s.enqueue('!r',e)
        class Client:
            rooms={}
            async def room_send(self,**kwargs):sends.append(kwargs);return RoomSendResponse('$reply'+kwargs['tx_id'],'!r')
        async def handler(room,e):
            nonlocal maximum
            assert e.sender not in active;active.add(e.sender);maximum=max(maximum,len(active));await asyncio.sleep(.002)
            order.setdefault(e.sender,[]).append(int(e.body))
            await dispatch.send(room.room_id,'m.room.message',{'msgtype':'m.text','body':e.body})
            active.remove(e.sender)
            if len(sends)==12:done.set()
        dispatch=RequestDispatcher(s,Client(),handler);ready=asyncio.Event();ready.set();task=asyncio.create_task(dispatch.run(ready,workers=2))
        await asyncio.wait_for(done.wait(),timeout=3);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert maximum==2 and all(values==sorted(values) for values in order.values())
        assert len({x['tx_id'] for x in sends})==12
        assert all(x['content']['org.sts.request_id']=='$'+x['content']['body'] for x in sends)
        assert s.pending()==0 and s.metrics()['states']['done']==12;s.close()
    asyncio.run(run())


def test_retry_blocks_only_its_own_thread_and_keeps_other_users_running(tmp_path):
    s=InboxStore(tmp_path/'inbox.db','test',clock=lambda:100)
    for i,user in [(1,'a'),(2,'a'),(3,'b')]:
        with s.db:s.enqueue('r',NS(event_id=str(i),sender=user,body='q',server_timestamp=100_001))
    assert s.claim()['event_id']=='1';s.retry('1',1)
    assert s.claim()['event_id']=='3' and s.claim() is None;s.close()


def test_promotion_is_a_preview_until_explicit_apply_and_keeps_old_artifact(tmp_path):
    import hashlib
    from knowledge_updates import promote
    root=tmp_path/'corpus';root.mkdir();(root/'documents').mkdir();old=root/'documents/law.pdf';old.write_bytes(b'%PDF-old')
    item={'id':'law','file':'documents/law.pdf','source':'https://adilet.zan.kz/rus/docs/K1500000414','sha256':hashlib.sha256(old.read_bytes()).hexdigest()}
    original={'documents':[item]};(root/'manifest.json').write_text(json.dumps(original))
    staged=tmp_path/'stage';receipt=check_source(item,staged,fetch=lambda _:b'%PDF-new')
    approval=approve_candidate(item,receipt,staged/receipt['artifact_file'],reviewer='Human test reviewer',version='Verified test edition',applicability_note='Explicit test scope')
    private_json(staged/'approval.json',approval)
    validate=lambda p:json.loads((p/'manifest.json').read_text())
    result=promote(root,staged/'law.check.json',staged/'approval.json',validator=validate)
    assert result['applied'] is False and json.loads((root/'manifest.json').read_text())==original
    result=promote(root,staged/'law.check.json',staged/'approval.json',apply=True,validator=validate)
    assert result['applied'] and old.read_bytes()==b'%PDF-old'
    assert json.loads((root/'manifest.json').read_text())['documents'][0]['sha256']==receipt['artifact_sha256']


def test_failed_candidate_validation_does_not_replace_manifest(tmp_path):
    import hashlib
    from knowledge_updates import promote
    root=tmp_path/'corpus';root.mkdir();(root/'documents').mkdir();old=root/'documents/law.pdf';old.write_bytes(b'%PDF-old')
    item={'id':'law','file':'documents/law.pdf','source':'https://halykbank.kz/law.pdf','sha256':hashlib.sha256(old.read_bytes()).hexdigest()};original={'documents':[item]};(root/'manifest.json').write_text(json.dumps(original))
    receipt=check_source(item,tmp_path/'stage',fetch=lambda _:b'%PDF-invalid')
    approval=approve_candidate(item,receipt,tmp_path/'stage'/receipt['artifact_file'],reviewer='Human test reviewer',version='Test',applicability_note='Test')
    private_json(tmp_path/'stage/approval.json',approval)
    def reject(_):raise ValueError('Incomplete candidate')
    with pytest.raises(ValueError):promote(root,tmp_path/'stage/law.check.json',tmp_path/'stage/approval.json',apply=True,validator=reject)
    assert json.loads((root/'manifest.json').read_text())==original


def test_named_bank_without_operation_gets_a_clarification_not_an_arbitrary_tariff():
    from response_guidance import request_clarification
    text=request_clarification('finance','А какая комиссия в Freedom?')
    assert 'продукт и операцию' in text
    assert request_clarification('finance','Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?') is None


def test_load_harness_rejects_reused_stage_before_creating_new_accounts(tmp_path):
    import runpy
    validate=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/run_vm_load.py'))['validate_stage']
    stage=tmp_path/'trial';stage.mkdir();(stage/'matrix-load-results.json').write_text('{"cleanup":{"generator_deactivated":true}}')
    with pytest.raises(ValueError,match='fresh directory'):validate(stage,tmp_path)
    assert validate(tmp_path/'new-trial',tmp_path)==tmp_path/'new-trial'


def test_load_harness_validates_scope_before_creating_directories(tmp_path):
    import runpy
    validate=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/run_vm_load.py'))['validate_stage']
    outside=tmp_path.parent/'outside-project'
    with pytest.raises(ValueError):validate(outside,tmp_path)
    with pytest.raises(ValueError):validate(tmp_path,tmp_path)
    assert not outside.exists()
