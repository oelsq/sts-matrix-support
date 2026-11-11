"""Explicit public-key comparison for Element clients without a SAS button."""
import asyncio
import base64
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
pytest.importorskip('vodozemac')
from nio import AsyncClient
from nio.crypto import OlmDevice
from matrix_crypto import client_options
from matrix_verification import DeviceVerification,public_fingerprint
from state_cipher import StateCipher

USER='@alice:test';BOT='@bot:test'


def client(tmp_path):
    value=AsyncClient('http://synthetic.invalid',BOT,**client_options({'MATRIX_E2EE_ENABLED':'1'},StateCipher(b'P'*32),tmp_path))
    value.restore_login(BOT,'BOT','synthetic-token');value.keys_query=AsyncMock(return_value=NS())
    key=base64.b64encode(bytes(range(32))).decode().rstrip('=')
    peer=OlmDevice(USER,'ALICE',{'ed25519':key,'curve25519':base64.b64encode(bytes(range(31,-1,-1))).decode().rstrip('=')})
    value.device_store.add(peer);value.store.save_device_keys({USER:{peer.id:peer}})
    return value,peer,key


@pytest.mark.parametrize('value',['not a device key','x'*200,'',None])
def test_invalid_public_fingerprint_is_rejected(value):
    with pytest.raises(ValueError):public_fingerprint(value)


def test_grouped_public_key_is_canonical():
    key=base64.b64encode(bytes(range(32))).decode().rstrip('=')
    assert public_fingerprint(' '.join(key[i:i+4] for i in range(0,len(key),4)))==key


def test_exact_manual_comparison_verifies_only_one_device_and_survives_restart(tmp_path):
    c,peer,key=client(tmp_path/'state')
    other=OlmDevice(USER,'OTHER',{'ed25519':'A'*43,'curve25519':'B'*43});c.device_store.add(other);c.store.save_device_keys({USER:{other.id:other}})
    c.blacklist_device(peer)
    service=DeviceVerification(c,tmp_path/'operator',lambda user:user==USER)
    async def queried_peer():
        assert USER in c.olm.users_for_key_query
        return NS()
    c.keys_query.side_effect=queried_peer
    asyncio.run(service.verify_fingerprint(USER,'ALICE',key))
    assert c.olm.is_device_verified(peer) and not c.olm.is_device_blacklisted(peer)
    assert not c.olm.is_device_verified(other)
    second,_,_=client(tmp_path/'state')
    assert second.olm.is_device_verified(second.device_store[USER]['ALICE'])
    asyncio.run(c.close());asyncio.run(second.close())


@pytest.mark.parametrize('user,device,key',[(USER,'ALICE','A'*43),('@mallory:test','ALICE',None),(USER,'UNKNOWN',None)])
def test_mismatched_unknown_or_unapproved_peer_is_never_trusted(tmp_path,user,device,key):
    c,peer,actual=client(tmp_path/'state');service=DeviceVerification(c,tmp_path/'operator',lambda value:value==USER)
    with pytest.raises(ValueError):asyncio.run(service.verify_fingerprint(user,device,key or actual))
    assert not c.olm.is_device_verified(peer);asyncio.run(c.close())


def test_step_requires_explicit_decision_and_records_no_success_for_bad_key(tmp_path):
    c,peer,key=client(tmp_path/'state');root=tmp_path/'operator';service=DeviceVerification(c,root,lambda value:value==USER)
    asyncio.run(service.step());assert not c.olm.is_device_verified(peer)
    p=root/'verification-fingerprints.json';p.write_text(json.dumps([{'user_id':USER,'device_id':'ALICE','public_fingerprint':'A'*43}]));p.chmod(0o600)
    asyncio.run(service.step());assert not c.olm.is_device_verified(peer) and not p.exists()
    assert json.loads((root/'verification-error.json').read_text())['code']=='fingerprint_not_verified'
    p.write_text(json.dumps([{'user_id':USER,'device_id':'ALICE','public_fingerprint':key}]));p.chmod(0o600)
    asyncio.run(service.step());assert c.olm.is_device_verified(peer) and not p.exists()
    assert not (root/'verification-error.json').exists()
    asyncio.run(c.close())
