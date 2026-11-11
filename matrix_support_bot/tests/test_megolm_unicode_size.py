"""Real Megolm regression: a long Russian rich answer must fit a Matrix event."""
import asyncio,hashlib,json
from pathlib import Path
import pytest
pytest.importorskip('vodozemac')
import nio
from nio import AsyncClient,Event,MatrixRoom
from nio.crypto.sessions import InboundGroupSession
from matrix_crypto import client_options
from state_cipher import StateCipher
from crypto_sdk_compat import patch_megolm_file,MEGOLM_OLD,MEGOLM_NEW,MEGOLM_EXPECTED,MEGOLM_PATCHED


def test_long_cyrillic_html_answer_fits_and_decrypts_without_content_changes(tmp_path):
    def client(user,device):
        c=AsyncClient('http://synthetic.invalid',user,device_id=device,**client_options({'MATRIX_E2EE_ENABLED':'1'},StateCipher(b'U'*32),tmp_path/device))
        c.restore_login(user,device,'synthetic-token');return c
    alice=client('@alice:test','ALICE');bob=client('@bot:test','BOT');room='!synthetic:test'
    alice.olm.create_outbound_group_session(room);session=alice.olm.outbound_group_sessions[room];session.shared=True
    keys=alice.olm.account.identity_keys
    incoming=InboundGroupSession(session.session_key,keys['ed25519'],keys['curve25519'],room)
    bob.olm.inbound_group_store.add(incoming)
    text=('Письменное требование о выплате заработной платы. Әңгіме. '*130)
    content={'msgtype':'m.text','body':text,'format':'org.matrix.custom.html','formatted_body':'<p>'+text+'</p>','org.sts.request_id':'$test','org.sts.reply_key':'stable'}
    legacy=json.dumps({'type':'m.room.message','content':content,'room_id':room},separators=(',',':'))
    assert len(legacy.encode())*4/3>65536
    encrypted=alice.olm.group_encrypt(room,{'type':'m.room.message','content':dict(content)})
    wire={'type':'m.room.encrypted','sender':alice.user,'event_id':'$reply','origin_server_ts':1,'content':encrypted}
    assert len(json.dumps(wire).encode())<50000
    event=Event.parse_event(wire);event.room_id=room
    decoded=bob.decrypt_event(event)
    assert decoded.body==text and decoded.formatted_body==content['formatted_body']
    assert decoded.source['content']['org.sts.request_id']=='$test'
    assert decoded.source['content']['org.sts.reply_key']=='stable'
    asyncio.run(alice.close());asyncio.run(bob.close())


def test_pinned_megolm_patch_is_idempotent_and_rejects_unknown_source(tmp_path):
    source=(Path(nio.__file__).parent/'crypto/olm_machine.py').read_text()
    original=source.replace(MEGOLM_NEW,MEGOLM_OLD)
    assert hashlib.sha256(original.encode()).hexdigest()==MEGOLM_EXPECTED
    path=tmp_path/'olm_machine.py';path.write_text(original)
    patch_megolm_file(path);once=path.read_bytes();patch_megolm_file(path)
    assert path.read_bytes()==once and hashlib.sha256(once).hexdigest()==MEGOLM_PATCHED
    path.write_text(original+'\n# unexpected upstream change\n');before=path.read_bytes()
    with pytest.raises(RuntimeError,match='Unexpected nio Megolm'):patch_megolm_file(path)
    assert path.read_bytes()==before
