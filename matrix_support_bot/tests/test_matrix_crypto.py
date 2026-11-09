"""Native encryption tests use real vodozemac keys, never trust-all fixtures."""
import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
pytest.importorskip('vodozemac')
from nio import AsyncClient, Event, MatrixRoom, RoomMessageText, MegolmEvent, ToDeviceEvent
from nio.api import Api
from nio.crypto import OlmDevice, Sas
from nio.crypto.sessions import InboundGroupSession
from matrix_crypto import MatrixCrypto, CryptoPolicyError, client_options
from matrix_inbox import InboxStore
from matrix_transport import MatrixSender
from matrix_verification import DeviceVerification
from state_cipher import StateCipher

ROOM='!synthetic:test';ALICE='@alice:test';BOB='@bot:test'


def native(tmp_path, user=ALICE, device='ALICE'):
    path=tmp_path/device
    cipher=StateCipher(b'K'*32)
    client=AsyncClient('http://synthetic.invalid',user,**client_options({'MATRIX_E2EE_ENABLED':'1'},cipher,path))
    client.restore_login(user,device,'synthetic-token')
    client.rooms[ROOM]=MatrixRoom(ROOM,user);client.rooms[ROOM].encrypted=True
    return client,MatrixCrypto(client,path/'crypto')


def encrypted(alice, ident='$one'):
    alice.olm.create_outbound_group_session(ROOM)
    group=alice.olm.outbound_group_sessions[ROOM];group.shared=True
    group.initial_key=group.session_key
    content=alice.olm.group_encrypt(ROOM,{'type':'m.room.message','content':{'msgtype':'m.text','body':'private synthetic message'}})
    event=Event.parse_event({'type':'m.room.encrypted','event_id':ident,'sender':ALICE,'origin_server_ts':101000,'content':content,'unsigned':{}})
    event.room_id=ROOM
    return event,group


def import_key(alice,bob,group):
    keys=alice.olm.account.identity_keys
    session=InboundGroupSession(group.initial_key,keys['ed25519'],keys['curve25519'],ROOM)
    bob.olm.inbound_group_store.add(session);bob.olm.save_inbound_group_session(session)
    device=OlmDevice(ALICE,'ALICE',keys)
    bob.device_store.add(device)
    bob.store.save_device_keys({ALICE:{device.id:device}})
    return device


def test_crypto_is_explicit_and_requires_protected_state(tmp_path):
    assert not client_options({},None,tmp_path)['config'].encryption_enabled
    with pytest.raises(CryptoPolicyError):client_options({'MATRIX_E2EE_ENABLED':'1'},None,tmp_path)


def test_identity_is_persistent_and_missing_store_is_not_recreated(tmp_path):
    alice,crypto=native(tmp_path)
    crypto.remember_identity()
    session={'user_id':ALICE,'device_id':'ALICE','access_token':'synthetic-token'}
    crypto.guard_existing_store(session)
    before=alice.olm.account.identity_keys
    again,guard=native(tmp_path)
    guard.guard_existing_store(session);guard.remember_identity()
    assert again.olm.account.identity_keys==before
    with pytest.raises(CryptoPolicyError):guard.guard_existing_store(None)
    with pytest.raises(CryptoPolicyError):guard.guard_existing_store(dict(session,device_id='OTHER'))
    for file in crypto.path.glob('*.db'):file.unlink()
    with pytest.raises(CryptoPolicyError):guard.guard_existing_store(session)


def test_real_megolm_requires_verified_device_and_survives_restart(tmp_path):
    alice,_=native(tmp_path);bob,crypto=native(tmp_path,BOB,'BOT')
    event,group=encrypted(alice)
    assert isinstance(event,MegolmEvent) and b'private synthetic message' not in json.dumps(event.source).encode()
    device=import_key(alice,bob,group)
    clear=bob.decrypt_event(event)
    assert clear.body=='private synthetic message' and clear.decrypted and not clear.verified
    assert crypto.classify(ROOM,clear)['kind']=='unverified'
    bob.verify_device(device)
    assert crypto.classify(ROOM,clear) is None
    bob2,crypto2=native(tmp_path,BOB,'BOT')
    assert bob2.olm.is_device_verified(bob2.device_store[ALICE]['ALICE'])
    assert crypto2.classify(ROOM,bob2.decrypt_event(event)) is None
    bob2.device_store[ALICE]['ALICE'].deleted=True
    assert not crypto2._trusted(clear,ROOM)


def test_missing_group_key_is_durable_and_retried_after_key_and_verification(tmp_path):
    alice,_=native(tmp_path);bob,crypto=native(tmp_path,BOB,'BOT')
    event,group=encrypted(alice)
    inbox=InboxStore(tmp_path/'queue','test',clock=lambda:100,cipher=StateCipher(b'K'*32))
    inbox.commit_sync('next',[(ROOM,event)],{ROOM:event.event_id},secure_events={event.event_id:crypto.classify(ROOM,event)})
    async def run():
        # No key: keep ciphertext and cursor safely, do not fabricate a reply.
        bob.request_room_key=lambda event:asyncio.sleep(0)
        assert await crypto.resume(inbox,lambda _:True,lambda *_:True)==0
        device=import_key(alice,bob,group)
        assert await crypto.resume(inbox,lambda _:True,lambda *_:True)==0
        bob.verify_device(device)
        assert await crypto.resume(inbox,lambda _:True,lambda *_:True)==1
        assert await crypto.resume(inbox,lambda _:True,lambda *_:True)==0
    asyncio.run(run())
    assert inbox.claim()['body']=='private synthetic message';inbox.close()


def test_plaintext_in_encrypted_room_is_rejected_and_not_downgraded(tmp_path):
    bob,crypto=native(tmp_path,BOB,'BOT')
    clear=Event.parse_event({'type':'m.room.message','event_id':'$plain','sender':ALICE,'origin_server_ts':101000,'content':{'msgtype':'m.text','body':'plain'}})
    assert crypto.classify(ROOM,clear) is False
    async def run():
        bob.olm=None
        sender=MatrixSender(None,bob,'http://synthetic.invalid',secure_room=lambda _:True)
        with pytest.raises(RuntimeError,match='no plaintext fallback'):
            await sender(room_id=ROOM,message_type='m.room.message',content={'body':'secret'},tx_id='stable')
    asyncio.run(run())


def test_sas_commitment_matches_matrix_spec_instead_of_self_consistent_hex():
    a=OlmDevice(ALICE,'ALICE',{'ed25519':'A'*43,'curve25519':'B'*43})
    b=OlmDevice(BOB,'BOT',{'ed25519':'C'*43,'curve25519':'D'*43})
    initiator=Sas(ALICE,'ALICE',a.ed25519,b,transaction_id='spec-test')
    start=initiator.start_verification()
    event=ToDeviceEvent.parse_event({'type':start.type,'sender':ALICE,'content':start.content})
    responder=Sas.from_key_verification_start(BOB,'BOT',b.ed25519,a,event)
    accepted=responder.accept_verification().content
    digest=hashlib.sha256((responder.pubkey+Api.to_canonical_json(start.content)).encode()).digest()
    assert accepted['commitment']==base64.b64encode(digest).decode().rstrip('=')
    assert len(accepted['commitment'])==43
    assert accepted['message_authentication_code']=='hkdf-hmac-sha256.v2'
    assert start.content['message_authentication_codes']==['hkdf-hmac-sha256.v2']


def test_operator_actions_never_auto_accept_emoji(tmp_path):
    calls=[]
    class Client:
        key_verifications={}
        async def confirm_short_auth_string(self,ident):calls.append(('match',ident))
        async def cancel_key_verification(self,ident,reject):calls.append(('reject',ident))
    service=DeviceVerification(Client(),tmp_path,lambda _:True,clock=lambda:100)
    service.pending['t']={'transaction_id':'t','created':100,'status':'compare_emoji','emoji':[['x','x']]*7}
    asyncio.run(service.step());assert calls==[]
    action=tmp_path/'verification-actions.json'
    action.write_text(json.dumps([{'transaction_id':'t','decision':'match'}]));action.chmod(0o600)
    asyncio.run(service.step());assert calls==[('match','t')]
    assert service.pending['t']['status']=='awaiting_peer_confirmation'
    assert not action.exists()


def test_unrequested_peer_cannot_negotiate_verification(tmp_path):
    bob,_=native(tmp_path,BOB,'BOT')
    service=DeviceVerification(bob,tmp_path/'operator',lambda _:False,clock=lambda:100)
    event=ToDeviceEvent.parse_event({'type':'m.key.verification.request','sender':ALICE,'content':{'transaction_id':'t','from_device':'ALICE','timestamp':100000,'methods':['m.sas.v1']}})
    asyncio.run(service.callback(event));assert service.pending=={}


def test_sas_full_exchange_checks_both_confirmation_and_mac():
    alice=OlmDevice(ALICE,'ALICE',{'ed25519':'A'*43,'curve25519':'B'*43})
    bob=OlmDevice(BOB,'BOT',{'ed25519':'C'*43,'curve25519':'D'*43})
    left=Sas(ALICE,'ALICE',alice.ed25519,bob,transaction_id='full-test')
    def event(message,sender):return ToDeviceEvent.parse_event({'type':message.type,'sender':sender,'content':message.content})
    right=Sas.from_key_verification_start(BOB,'BOT',bob.ed25519,alice,event(left.start_verification(),ALICE))
    left.receive_accept_event(event(right.accept_verification(),BOB))
    right.receive_key_event(event(left.share_key(),ALICE))
    left.receive_key_event(event(right.share_key(),BOB))
    assert left.get_emoji()==right.get_emoji() and len(left.get_emoji())==7
    assert not left.verified and not right.verified
    left.accept_sas();right.accept_sas()
    left.receive_mac_event(event(right.get_mac(),BOB))
    right.receive_mac_event(event(left.get_mac(),ALICE))
    assert left.verified and right.verified


def test_sas_emoji_uses_native_indices_without_repacking_as_bytes():
    alice=OlmDevice(ALICE,'ALICE',{'ed25519':'A'*43,'curve25519':'B'*43})
    bob=OlmDevice(BOB,'BOT',{'ed25519':'C'*43,'curve25519':'D'*43})
    left=Sas(ALICE,'ALICE',alice.ed25519,bob,transaction_id='emoji-index-spec')
    def event(message,sender):return ToDeviceEvent.parse_event({'type':message.type,'sender':sender,'content':message.content})
    right=Sas.from_key_verification_start(BOB,'BOT',bob.ed25519,alice,event(left.start_verification(),ALICE))
    left.receive_accept_event(event(right.accept_verification(),BOB))
    right.receive_key_event(event(left.share_key(),ALICE));left.receive_key_event(event(right.share_key(),BOB))
    indices=left.established_sas.bytes(left._extra_info).emoji_indices
    assert len(indices)==7 and all(0<=i<64 for i in indices)
    assert left.get_emoji()==[Sas.emoji[i] for i in indices]


def test_operator_can_request_specific_device_but_ready_cannot_be_spoofed(tmp_path):
    alice,_=native(tmp_path);bob,_=native(tmp_path,BOB,'BOT')
    device=OlmDevice(ALICE,'ALICE',alice.olm.account.identity_keys);bob.device_store.add(device)
    sent=[]
    async def query():return NS()
    async def send(message):sent.append(message);return NS()
    bob.keys_query=query;bob.to_device=send
    service=DeviceVerification(bob,tmp_path/'operator',lambda user:user==ALICE,clock=lambda:100)
    async def run():
        await service.initiate(ALICE,'ALICE')
        assert sent[-1].type=='m.key.verification.request'
        ident=sent[-1].content['transaction_id']
        assert service.pending[ident]['status']=='requested_by_bot'
        def ready(sender):return ToDeviceEvent.parse_event({'type':'m.key.verification.ready','sender':sender,'content':{'transaction_id':ident,'from_device':'ALICE','methods':['m.sas.v1']}})
        await service.callback(ready('@mallory:test'));assert len(sent)==1
        await service.callback(ready(ALICE));assert sent[-1].type=='m.key.verification.start'
        assert service.pending[ident]['status']=='negotiating'
        assert not bob.olm.is_device_verified(device)
        with pytest.raises(ValueError):await service.initiate('@mallory:test','ALICE')
    asyncio.run(run())


def test_verification_status_can_be_created_before_matrix_login(tmp_path):
    client=AsyncClient('http://synthetic.invalid',BOB)
    verifier=DeviceVerification(client,tmp_path/'before-login',lambda _:False)
    assert json.loads((tmp_path/'before-login/verification-status.json').read_text())['devices']==[]
