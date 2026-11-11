"""SAS verification requires an explicit local operator decision; never trust-all."""
import json
import base64
import hmac
import time
from pathlib import Path
from nio import (KeyVerificationStart,KeyVerificationKey,KeyVerificationMac,
                 KeyVerificationCancel,ToDeviceMessage)
from matrix_crypto import private_json


def public_fingerprint(value):
    """Canonical public Ed25519 fingerprint, never a recovery/private key."""
    if not isinstance(value,str) or len(value)>256:
        raise ValueError('Invalid public device fingerprint')
    text=''.join(value.split())
    try:
        raw=base64.b64decode(text+'='*(-len(text)%4),altchars=b'-_',validate=True)
    except (ValueError,UnicodeError):
        raise ValueError('Invalid public device fingerprint') from None
    if len(raw)!=32:raise ValueError('Expected a public Ed25519 fingerprint')
    return base64.b64encode(raw).decode().rstrip('=')


class DeviceVerification:
    def __init__(self, client, directory, allowed_peer, *, clock=time.time):
        self.client,self.path,self.allowed_peer,self.clock=client,Path(directory),allowed_peer,clock
        self.pending={}
        self.decided=set()
        self.outgoing={}
        self._publish()

    def _publish(self):
        store=self.client.device_store if getattr(self.client,'olm',None) is not None else []
        devices=[{'user_id':d.user_id,'device_id':d.id,'display_name':d.display_name,'verified':d.verified,'deleted':d.deleted} for d in store if d.user_id!=getattr(self.client,'user',None) and self.allowed_peer(d.user_id)]
        private_json(self.path/'verification-status.json',{'version':1,'requests':list(self.pending.values()),'devices':devices[:200]})

    async def callback(self, event):
        source=getattr(event,'source',{})
        if source.get('type')=='m.key.verification.request':
            await self._request(event)
            return
        if source.get('type')=='m.key.verification.ready':
            content=source.get('content',{});ident=content.get('transaction_id');request=self.pending.get(ident)
            if request and ident in self.outgoing and request['user_id']==event.sender and request['device_id']==content.get('from_device') and 'm.sas.v1' in content.get('methods',[]):
                result=await self.client.to_device(self.outgoing[ident])
                if not getattr(result,'status_code',None):
                    self.outgoing.pop(ident,None);request['status']='negotiating';self._publish()
            return
        ident=getattr(event,'transaction_id',None)
        if not isinstance(ident,str) or not 1<=len(ident)<=256:return
        if isinstance(event,KeyVerificationStart):
            existing=self.pending.get(ident)
            if existing and (existing['user_id']!=event.sender or existing['device_id']!=event.from_device):
                await self.client.cancel_key_verification(ident,reject=True)
                return
            if not self.allowed_peer(event.sender) or (len(self.pending)>=20 and ident not in self.pending):
                await self.client.cancel_key_verification(ident,reject=True)
                return
            if 'emoji' not in event.short_authentication_string:
                await self.client.cancel_key_verification(ident,reject=True)
                return
            if ident not in self.client.key_verifications:return
            result=await self.client.accept_key_verification(ident)
            if getattr(result,'status_code',None):return
            sas=self.client.key_verifications.get(ident)
            if sas:
                self.pending[ident]={'transaction_id':ident,'user_id':event.sender,'device_id':sas.other_olm_device.id,'created':self.clock(),'status':'negotiating'}
        elif isinstance(event,KeyVerificationKey):
            sas=self.client.key_verifications.get(ident)
            if sas and ident in self.pending:
                self.pending[ident].update(status='compare_emoji',emoji=[list(x) for x in sas.get_emoji()])
        elif isinstance(event,KeyVerificationCancel):
            self.pending.pop(ident,None);self.outgoing.pop(ident,None);self.decided.discard(ident)
        elif isinstance(event,KeyVerificationMac):
            sas=self.client.key_verifications.get(ident)
            if sas and sas.verified:
                await self._complete(ident,sas)
        self._publish()

    async def _complete(self, ident, sas):
        peer=sas.other_olm_device
        response=await self.client.to_device(ToDeviceMessage('m.key.verification.done',peer.user_id,peer.id,{'transaction_id':ident}))
        if not getattr(response,'status_code',None):
            self.pending.pop(ident,None)
            self.decided.discard(ident)
            self.outgoing.pop(ident,None)

    async def _request(self, event):
        content=event.source.get('content',{})
        ident=content.get('transaction_id');device=content.get('from_device');stamp=content.get('timestamp')
        if (not self.allowed_peer(event.sender) or not isinstance(ident,str) or not 1<=len(ident)<=256
                or not isinstance(device,str) or not 1<=len(device)<=256 or not isinstance(stamp,(int,float))
                or abs(self.clock()*1000-stamp)>300000 or 'm.sas.v1' not in content.get('methods',[])
                or len(self.pending)>=20):return
        self.client.olm.users_for_key_query.add(event.sender)
        response=await self.client.keys_query()
        if getattr(response,'status_code',None):return
        peer=self.client.device_store[event.sender].get(device)
        if peer is None:return
        ready=ToDeviceMessage('m.key.verification.ready',event.sender,device,
                              {'transaction_id':ident,'from_device':self.client.device_id,'methods':['m.sas.v1']})
        response=await self.client.to_device(ready)
        if getattr(response,'status_code',None):return
        self.pending[ident]={'transaction_id':ident,'user_id':event.sender,'device_id':device,'created':self.clock(),'status':'requested'}
        self._publish()

    async def initiate(self, user, device_id):
        if not self.allowed_peer(user) or len(self.pending)>=20:raise ValueError('Verification peer is not authorized')
        self.client.olm.users_for_key_query.add(user)
        response=await self.client.keys_query()
        if getattr(response,'status_code',None):raise RuntimeError('Device query failed')
        peer=self.client.device_store[user].get(device_id)
        if peer is None or peer.deleted or peer.blacklisted:raise ValueError('Active device required')
        if any(r['user_id']==user and r['device_id']==device_id for r in self.pending.values()):return
        start=self.client.create_key_verification(peer);ident=start.content['transaction_id']
        response=await self.client.to_device(ToDeviceMessage('m.key.verification.request',user,device_id,{'transaction_id':ident,'from_device':self.client.device_id,'timestamp':int(self.clock()*1000),'methods':['m.sas.v1']}))
        if getattr(response,'status_code',None):raise RuntimeError('Verification request was not delivered')
        self.outgoing[ident]=start
        self.pending[ident]={'transaction_id':ident,'user_id':user,'device_id':device_id,'created':self.clock(),'status':'requested_by_bot'}
        self._publish()

    async def verify_fingerprint(self,user,device_id,expected):
        """Manual out-of-band comparison of one device; no trust-all fallback."""
        if not isinstance(user,str) or not self.allowed_peer(user) or not isinstance(device_id,str) or not 1<=len(device_id)<=256:
            raise ValueError('Unapproved fingerprint peer')
        expected=public_fingerprint(expected)
        self.client.olm.users_for_key_query.add(user)
        result=await self.client.keys_query()
        if getattr(result,'status_code',None):
            raise RuntimeError('Device keys are unavailable; verification deferred')
        try:peer=self.client.device_store[user][device_id]
        except KeyError:raise ValueError('Unknown fingerprint device') from None
        if peer.deleted or not hmac.compare_digest(public_fingerprint(peer.ed25519),expected):
            raise ValueError('Public device fingerprint does not match')
        self.client.verify_device(peer)
        self._publish()

    async def step(self):
        fingerprints=self.path/'verification-fingerprints.json'
        if fingerprints.exists():
            from state_cipher import read_private_secret
            try:
                values=json.loads(read_private_secret(fingerprints))
                if (not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],dict)
                        or set(values[0])!={'user_id','device_id','public_fingerprint'}):
                    raise ValueError('Invalid explicit fingerprint decision')
                value=values[0]
                await self.verify_fingerprint(value['user_id'],value['device_id'],value['public_fingerprint'])
            except ValueError:
                private_json(self.path/'verification-error.json',{'code':'fingerprint_not_verified'})
                fingerprints.unlink();return
            (self.path/'verification-error.json').unlink(missing_ok=True)
            fingerprints.unlink()
        requests=self.path/'verification-requests.json'
        if requests.exists():
            from state_cipher import read_private_secret
            values=json.loads(read_private_secret(requests))
            if not isinstance(values,list) or len(values)>10:raise ValueError('Invalid verification requests')
            for value in values:
                if not isinstance(value,dict) or set(value)!={'user_id','device_id'}:raise ValueError('Invalid verification request')
                await self.initiate(value['user_id'],value['device_id'])
            requests.unlink()
        for ident,value in list(self.pending.items()):
            sas=self.client.key_verifications.get(ident)
            if sas and sas.verified:
                await self._complete(ident,sas)
                continue
            if self.clock()-value['created']>300:
                self.pending.pop(ident,None)
                self.decided.discard(ident)
                self.outgoing.pop(ident,None)
                try:await self.client.cancel_key_verification(ident,reject=True)
                except Exception:pass
        action=self.path/'verification-actions.json'
        if action.exists():
            info=action.stat()
            if action.is_symlink() or info.st_mode&0o077:raise ValueError('Verification decisions must be private')
            if info.st_size>16384:raise ValueError('Oversized verification decision')
            values=json.loads(action.read_text())
            if not isinstance(values,list) or len(values)>20:raise ValueError('Invalid verification decisions')
            for value in values:
                if not isinstance(value,dict) or set(value)!={'transaction_id','decision'}:raise ValueError('Invalid verification decision')
                ident=value['transaction_id']
                if ident in self.decided:continue
                request=self.pending.get(ident)
                if not request:continue
                if value['decision']=='match' and request['status']!='compare_emoji':continue
                if value['decision']=='match':
                    await self.client.confirm_short_auth_string(ident)
                    request['status']='awaiting_peer_confirmation'
                elif value['decision']=='reject':
                    await self.client.cancel_key_verification(ident,reject=True)
                    self.pending.pop(ident,None)
                else:raise ValueError('Unknown verification decision')
                self.decided.add(ident)
            action.unlink()
        self._publish()
