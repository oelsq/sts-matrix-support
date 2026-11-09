import base64
import json
from types import SimpleNamespace as NS

import pytest

from state_cipher import StateCipher, StateEncryptionError
from matrix_inbox import InboxStore
from human_handoff import HandoffStore


def message(ident='$one',body='synthetic-private-question',timestamp=101000):
    return NS(event_id=ident,sender='@synthetic:test',body=body,server_timestamp=timestamp)


def test_aead_is_randomized_and_bound_to_field_and_record():
    cipher=StateCipher(b'A'*32)
    left=cipher.seal('private','row:1:body');right=cipher.seal('private','row:1:body')
    assert left!=right and 'private' not in left
    assert cipher.open(left,'row:1:body')=='private'
    for purpose,key,value in [('row:2:body',cipher,left),('row:1:body',StateCipher(b'B'*32),left),('row:1:body',cipher,left[:-4]+'AAAA')]:
        with pytest.raises(StateEncryptionError):key.open(value,purpose)


def test_key_must_be_private_and_e2ee_cannot_silently_disable_storage(tmp_path):
    path=tmp_path/'key';path.write_bytes(base64.urlsafe_b64encode(b'K'*32));path.chmod(0o644)
    with pytest.raises(StateEncryptionError):StateCipher.from_env({'STATE_ENCRYPTION_KEY_FILE':str(path)})
    path.chmod(0o600);assert StateCipher.from_env({'STATE_ENCRYPTION_KEY_FILE':str(path)}).key_id
    link=tmp_path/'linked';link.symlink_to(path)
    with pytest.raises(StateEncryptionError):StateCipher.from_env({'STATE_ENCRYPTION_KEY_FILE':str(link)})
    with pytest.raises(StateEncryptionError):StateCipher.from_env({'MATRIX_E2EE_ENABLED':'1'})


def test_existing_queue_session_and_history_migrate_and_recover_after_restart(tmp_path):
    path=tmp_path/'inbox.sqlite3';clock=lambda:100
    s=InboxStore(path,'synthetic',clock=clock)
    s.commit_sync('cursor',[('!room:test',message())],{'!room:test':'$one'})
    s.set_meta('matrix_session',json.dumps({'access_token':'synthetic-private-token'}))
    s.save_history('!room:test','@synthetic:test','legal',['synthetic-private-history'])
    with s.db:s.save_context('$one','legal','synthetic-private-effective','synthetic-private-previous')
    s.prepare_reply('$one','final',{'body':'synthetic-private-answer'},True)
    original_tx=s.transaction_id('$one','final');s.close()
    cipher=StateCipher(b'K'*32);s=InboxStore(path,'synthetic',clock=clock,cipher=cipher)
    assert s.cursor=='cursor' and s.claim()['body']=='synthetic-private-question'
    assert 'synthetic-private-token' in s.meta('matrix_session')
    assert s.load_history('!room:test','@synthetic:test','legal',1800)==['synthetic-private-history']
    assert s.prepared_context('$one','legal')==('synthetic-private-effective','synthetic-private-previous')
    assert 'synthetic-private-answer' in s.replies('$one')[0]['content']
    assert s.transaction_id('$one','final')==original_tx
    s.close()
    assert b'synthetic-private-' not in path.read_bytes()
    with pytest.raises(StateEncryptionError):InboxStore(path,'synthetic',clock=clock)
    with pytest.raises(StateEncryptionError):InboxStore(path,'synthetic',clock=clock,cipher=StateCipher(b'W'*32))
    s=InboxStore(path,'synthetic',clock=clock,cipher=cipher)
    assert s.claim()['event_id']=='$one';s.finish('$one');assert s.pending()==0;s.close()


def test_missing_key_wait_preserves_user_order_and_deduplication(tmp_path):
    s=InboxStore(tmp_path/'inbox','synthetic',clock=lambda:100,cipher=StateCipher(b'K'*32))
    first=message('$encrypted');second=message('$later')
    pending={'$encrypted':{'kind':'ciphertext','source':{'ciphertext':'synthetic-private-cipher'}}}
    s.commit_sync('next',[('!room:test',first),('!room:test',second)],{'!room:test':'$later'},secure_events=pending)
    assert s.cursor=='next' and s.pending()==2 and s.claim() is None
    assert s.secure_pending()[0]['payload']['kind']=='ciphertext'
    s.commit_sync('next2',[('!room:test',first)],{},secure_events=pending)
    assert s.pending()==2
    with pytest.raises(ValueError):s.resolve_secure('!other:test',first)
    assert s.resolve_secure('!room:test',first)
    assert not s.resolve_secure('!room:test',first)
    assert s.claim()['event_id']=='$encrypted';s.finish('$encrypted')
    assert s.claim()['event_id']=='$later';s.close()


def test_handoff_context_is_encrypted_in_current_and_archived_records(tmp_path):
    path=tmp_path/'handoff';cipher=StateCipher(b'K'*32)
    store=HandoffStore(path,cipher=cipher,clock=lambda:100)
    store.create('@synthetic:test','legal','!first:test','@staff1:test',context=['synthetic-private-work','synthetic-private-response'],note='synthetic-private-note',encrypted=True)
    assert json.loads(store.record('@synthetic:test','legal')['context_json'])[0]=='synthetic-private-work'
    store.create('@synthetic:test','legal','!second:test','@staff2:test',context=['next','answer'],encrypted=True)
    assert store.encrypted_room('!first:test')
    assert json.loads(store.room_record('!first:test')['context_json'])[0]=='synthetic-private-work'
    store.db.close();assert b'synthetic-private-' not in path.read_bytes()
    assert path.stat().st_mode&0o777==0o600
    with pytest.raises(StateEncryptionError):HandoffStore(path)
    reopened=HandoffStore(path,cipher=cipher,clock=lambda:100)
    assert reopened.record('@synthetic:test','legal')['room']=='!second:test';reopened.db.close()


def test_handoff_retention_erases_copies_but_keeps_room_mapping(tmp_path):
    now=[100];store=HandoffStore(tmp_path/'handoff',cipher=StateCipher(b'K'*32),clock=lambda:now[0],retention_days=1)
    store.create('@synthetic:test','finance','!room:test','@staff:test',context=['private','answer'],note='private')
    now[0]+=86401;store.expire_context()
    row=store.record('@synthetic:test','finance')
    assert row['room']=='!room:test' and row['context_json'] is None and row['note']==''
    store.db.close()
