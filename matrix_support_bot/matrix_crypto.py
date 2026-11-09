"""Native Matrix E2EE: persistent identity, verified devices and durable key waits."""
import asyncio
import hashlib
import json
import logging
import os
import time
import tempfile
from pathlib import Path
from types import SimpleNamespace

from nio import AsyncClientConfig, Event, MegolmEvent, RoomMessageText
from state_cipher import private_directory

log = logging.getLogger('support_bot.crypto')


class CryptoPolicyError(ValueError):
    pass


def private_json(path, value):
    path = Path(path)
    private_directory(path.parent)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    tmp=Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def client_options(env, cipher, state_path):
    enabled = env.get('MATRIX_E2EE_ENABLED') == '1'
    if not enabled:
        return {'config':AsyncClientConfig(encryption_enabled=False)}
    from nio.crypto import ENCRYPTION_ENABLED
    from nio.crypto import sas
    if getattr(sas,'STS_SAS_WIRE_COMPAT',None)!='matrix-spec-v2':
        raise CryptoPolicyError('Pinned Matrix SAS compatibility patch is required')
    if not ENCRYPTION_ENABLED or cipher is None:
        raise CryptoPolicyError('Native E2EE dependencies and protected state are required')
    store=private_directory(Path(state_path)/'crypto')
    return {'store_path':str(store), 'config':AsyncClientConfig(
        encryption_enabled=True,pickle_key=cipher.crypto_pickle_key(),
        store_sync_tokens=False,max_limit_exceeded=0,max_timeouts=0,
        request_timeout=20)}


class MatrixCrypto:
    def __init__(self, client, store_path, *, clock=time.time, require_verified=True):
        self.client=client
        self.path=private_directory(store_path)
        self.clock=clock
        self.require_verified=require_verified
        self.metrics={'deferred':0,'resumed':0,'rejected_plaintext':0,'waiting_verification':0}
        self._requested=set()

    def guard_existing_store(self, session):
        marker=self.path/'identity.json'
        databases=list(self.path.glob('*.db'))
        if marker.exists() and not session:
            raise CryptoPolicyError('Saved crypto identity requires its matching session')
        if (marker.exists() and not databases) or (databases and not session):
            raise CryptoPolicyError('Crypto identity is incomplete; restore its protected backup')
        if marker.exists():
            saved=json.loads(marker.read_text())
            if (saved.get('user_id'),saved.get('device_id'))!=(session.get('user_id'),session.get('device_id')):
                raise CryptoPolicyError('Saved crypto device must not be replaced automatically')

    def remember_identity(self):
        olm=getattr(self.client,'olm',None)
        if olm is None:raise CryptoPolicyError('Crypto store is not loaded')
        fingerprint=hashlib.sha256(olm.account.identity_keys['ed25519'].encode()).hexdigest()
        value={'user_id':self.client.user_id,'device_id':self.client.device_id,'fingerprint_sha256':fingerprint}
        path=self.path/'identity.json'
        if path.exists() and json.loads(path.read_text())!=value:
            raise CryptoPolicyError('Crypto device identity changed unexpectedly')
        if not path.exists():private_json(path,value)
        for file in self.path.iterdir():
            if file.is_file() and not file.is_symlink():file.chmod(0o600)

    def _trusted(self, event, room_id=None):
        if not getattr(event,'decrypted',False):return False
        if not self.require_verified:return True
        try:
            key=getattr(event,'sender_key','')
            session=self.client.olm.inbound_group_store.get(room_id or event.room_id,key,event.session_id)
            if session is None or session.forwarding_chain:return False
            devices=self.client.device_store[event.sender].values()
            return any(device.curve25519==key and device.ed25519==session.ed25519
                       and not device.deleted and not self.client.olm.is_device_blacklisted(device)
                       and self.client.olm.is_device_verified(device) for device in devices)
        except (KeyError,AttributeError):return False

    def classify(self, room_id, event):
        room=self.client.rooms.get(room_id)
        encrypted=bool(room and room.encrypted)
        if isinstance(event,MegolmEvent):
            if not encrypted:raise CryptoPolicyError('Encrypted event has no encrypted room state')
            self.metrics['deferred']+=1
            return {'kind':'ciphertext','source':event.source}
        if encrypted and isinstance(event,RoomMessageText):
            if not getattr(event,'decrypted',False):
                self.metrics['rejected_plaintext']+=1
                return False
            if not self._trusted(event,room_id):
                self.metrics['waiting_verification']+=1
                return {'kind':'unverified','source':event.source,
                        'body':event.body,'timestamp':event.server_timestamp,
                        'session_id':getattr(event,'session_id',''),
                        'sender_key':getattr(event,'sender_key','')}
        return None

    async def resume(self, inbox, eligible_room, eligible_event):
        resumed=0
        for row in inbox.secure_pending():
            if not eligible_room(row['room_id']):continue
            payload=row['payload']
            if payload.get('kind')=='ciphertext':
                event=None
                try:
                    event=Event.parse_event(payload['source'])
                    if not isinstance(event,MegolmEvent):raise CryptoPolicyError('Invalid buffered encrypted event')
                    event.room_id=row['room_id']
                    event=self.client.decrypt_event(event)
                except Exception:
                    if isinstance(event,MegolmEvent) and row['event_id'] not in self._requested and len(self._requested)<1000:
                        self._requested.add(row['event_id'])
                        try:
                            await asyncio.wait_for(self.client.request_room_key(event),timeout=10)
                        except Exception:pass
                    continue
            elif payload.get('kind')=='unverified':
                event=SimpleNamespace(event_id=row['event_id'],sender=row['sender'],body=payload['body'],
                                      server_timestamp=payload['timestamp'],decrypted=True,verified=False,
                                      session_id=payload['session_id'],sender_key=payload['sender_key'],room_id=row['room_id'])
            else:raise CryptoPolicyError('Invalid protected encrypted queue entry')
            if not hasattr(event,'body') or not self._trusted(event,row['room_id']):continue
            if not eligible_event(row['room_id'],event):continue
            if inbox.resolve_secure(row['room_id'],event):resumed+=1
        self.metrics['resumed']+=resumed
        return resumed
