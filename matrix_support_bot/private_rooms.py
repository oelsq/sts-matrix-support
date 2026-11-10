"""Per-user encrypted specialist rooms; an AI room never mixes its owners."""
import asyncio
import hashlib
import hmac
import json
from urllib.parse import quote
from nio import RoomCreateResponse, RoomPreset

from matrix_crypto import CryptoPolicyError

ALGORITHM='m.megolm.v1.aes-sha2'


class PrivateRooms:
    def __init__(self, inbox, client, fixed_routes, public_url, cipher, *, free_mode=None):
        if cipher is None:raise CryptoPolicyError('Private AI rooms need protected state')
        self.inbox,self.client,self.fixed,self.cipher=inbox,client,fixed_routes,cipher
        self.public_url=public_url.rstrip('/')
        self.free_mode=free_mode
        self.free_domains={domain for room,domain in (free_mode.routes.items() if free_mode else []) if fixed_routes.get(room)==domain}
        self.locks={}
        self.inbox.db.execute('CREATE TABLE IF NOT EXISTS private_ai_rooms (owner TEXT,domain TEXT,room TEXT UNIQUE,alias TEXT NOT NULL,PRIMARY KEY(owner,domain))')
        self.inbox.db.commit()
        self.records={}
        for row in self.inbox.db.execute('SELECT owner,domain,room FROM private_ai_rooms WHERE room IS NOT NULL'):
            self._attach(*row)

    def _attach(self, owner, domain, room):
        self.records[room]={'owner':owner,'domain':domain}
        self.fixed[room]=domain
        if self.free_mode and domain in self.free_domains:self.free_mode.routes[room]=domain

    def known_peer(self, user):
        return any(value['owner']==user for value in self.records.values())

    def required_encryption(self, room):
        return room in self.records

    def permitted(self, room, sender):
        record=self.records.get(room)
        if record is None:return True
        if record['owner']!=sender:return False
        state=self.client.rooms.get(room)
        if state is None or not state.encrypted:return False
        # Room members must never silently expand a private conversation.
        return set(state.users)<=set([self.client.user,record['owner']])

    async def ensure(self, owner, domain):
        if domain not in {'legal','finance'} or not owner.startswith('@'):
            raise CryptoPolicyError('Invalid private specialist request')
        async with self.locks.setdefault((owner,domain),asyncio.Lock()):
            row=self.inbox.db.execute('SELECT room,alias FROM private_ai_rooms WHERE owner=? AND domain=?',(owner,domain)).fetchone()
            if row and row['room']:
                self._attach(owner,domain,row['room']);return row['room']
            alias=row['alias'] if row else self.cipher.routing_token(owner+'\0'+domain)
            if row is None:
                with self.inbox.db:self.inbox.db.execute('INSERT INTO private_ai_rooms VALUES(?,?,NULL,?)',(owner,domain,alias))
            title='ЮрАгент' if domain=='legal' else 'ФинАгент'
            response=await self.client.room_create(
                name='STS — '+title+' — личная комната',alias=alias,
                topic='Личная комната с ИИ. Перед первым ответом подтвердите устройство бота в Element. Вопрос расшифровывается ботом для локальной обработки моделью.',
                is_direct=True,preset=RoomPreset.private_chat,federate=False,invite=[owner],
                initial_state=[{'type':'m.room.encryption','state_key':'','content':{'algorithm':ALGORITHM}},
                               {'type':'m.room.history_visibility','state_key':'','content':{'history_visibility':'joined'}},
                               {'type':'m.room.guest_access','state_key':'','content':{'guest_access':'forbidden'}}],
                power_level_override={'users':{self.client.user:100,owner:0},'invite':100,'state_default':100})
            room=getattr(response,'room_id',None) if isinstance(response,RoomCreateResponse) else None
            if room is None and getattr(response,'status_code',None)=='M_ROOM_IN_USE':
                server=self.client.user.split(':',1)[1]
                resolved=await self.client.room_resolve_alias('#'+alias+':'+server)
                room=getattr(resolved,'room_id',None)
                # A resolved alias alone does not authorize adoption of a room.
                if room:
                    state=await self.client.room_get_state(room)
                    events=getattr(state,'events',None)
                    if not isinstance(events,list):raise CryptoPolicyError('Cannot verify recovered private room')
                    mapping={e.get('type'):e.get('content',{}) for e in events if e.get('state_key')==''}
                    create=mapping.get('m.room.create',{})
                    origin=next((e for e in events if e.get('type')=='m.room.create' and e.get('state_key')==''),{})
                    if create.get('creator',origin.get('sender'))!=self.client.user:
                        raise CryptoPolicyError('Recovered private room has an unexpected creator')
                    if mapping.get('m.room.encryption',{}).get('algorithm')!=ALGORITHM or create.get('m.federate',True):
                        raise CryptoPolicyError('Recovered room is not a private encrypted room')
                    members={e.get('state_key') for e in events if e.get('type')=='m.room.member' and e.get('content',{}).get('membership') in {'join','invite'}}
                    if (not members <= {self.client.user,owner}
                            or mapping.get('m.room.history_visibility',{}).get('history_visibility')!='joined'
                            or mapping.get('m.room.guest_access',{}).get('guest_access')!='forbidden'
                            or mapping.get('m.room.join_rules',{}).get('join_rule')!='invite'):
                        raise CryptoPolicyError('Recovered room has unsafe access rules')
            if not isinstance(room,str) or not room.startswith('!'):
                raise CryptoPolicyError('Could not create private encrypted specialist room')
            with self.inbox.db:self.inbox.db.execute('UPDATE private_ai_rooms SET room=? WHERE owner=? AND domain=?',(room,owner,domain))
            self._attach(owner,domain,room)
            return room

    async def invitation(self, owner, domain=None):
        roles=[domain] if domain in {'legal','finance'} else ['legal','finance']
        links=[]
        for role in roles:
            room=await self.ensure(owner,role)
            title='ЮрАгент' if role=='legal' else 'ФинАгент'
            links.append('- '+title+': '+self.public_url+'/#/room/'+quote(room,safe='!:'))
        return ('**Ваши личные комнаты с ИИ**\n\n'+'\n'.join(links)+
                '\n\nПримите приглашение и задайте вопрос там. Перед первым ответом подтвердите устройство бота через проверку эмодзи в Element. '+
                'Предыдущие сообщения этой общей комнаты автоматически не копируются.')

    async def operator_step(self, directory):
        """A local operator can invite a user without reading their password."""
        from pathlib import Path
        from state_cipher import read_private_secret
        from matrix_crypto import private_json
        path=Path(directory)/'room-actions.json'
        if not path.exists():return
        actions=json.loads(read_private_secret(path))
        if not isinstance(actions,list) or len(actions)>10:raise CryptoPolicyError('Invalid room actions')
        result=[]
        for action in actions:
            if not isinstance(action,dict) or set(action)!={'user_id','domain'}:raise CryptoPolicyError('Invalid room action')
            user,domain=action['user_id'],action['domain']
            if domain not in {'legal','finance'}:raise CryptoPolicyError('Invalid specialist')
            room=await self.ensure(user,domain)
            result.append({'user_id':user,'domain':domain,'room_id':room})
        private_json(Path(directory)/'room-status.json',{'rooms':result})
        path.unlink()
