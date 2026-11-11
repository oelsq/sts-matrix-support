"""Encryption transports the same Qwen request/answer; it is not an editor."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
pytest.importorskip('vodozemac')
from nio import AsyncClient,Event,MatrixRoom
from nio.crypto import OlmDevice
from nio.crypto.sessions import InboundGroupSession
from free_reply import FreeReplyMode,free_messages
from message_format import matrix_content
from matrix_crypto import client_options,MatrixCrypto,verified_recipients_only,CryptoPolicyError
from state_cipher import StateCipher

ROOM='!synthetic-contract:test'
USER='@alice:test';BOT='@bot:test'


def native(path,user,device):
    c=AsyncClient('http://synthetic.invalid',user,**client_options({'MATRIX_E2EE_ENABLED':'1'},StateCipher(b'T'*32),path/device))
    c.restore_login(user,device,'synthetic-token')
    c.rooms[ROOM]=MatrixRoom(ROOM,user);c.rooms[ROOM].encrypted=True
    guard=MatrixCrypto(c,path/device/'crypto');guard.remember_identity()
    return c,guard


def transfer(source,target,content,ident):
    source.olm.create_outbound_group_session(ROOM)
    group=source.olm.outbound_group_sessions[ROOM];key=group.session_key;group.shared=True
    keys=source.olm.account.identity_keys
    inbound=InboundGroupSession(key,keys['ed25519'],keys['curve25519'],ROOM)
    target.olm.inbound_group_store.add(inbound);target.olm.save_inbound_group_session(inbound)
    peer=OlmDevice(source.user,source.device_id,keys);target.device_store.add(peer)
    target.store.save_device_keys({source.user:{peer.id:peer}})
    # Only these two synthetic devices are controlled by this test fixture.
    target.verify_device(peer)
    encrypted=source.olm.group_encrypt(ROOM,{'type':'m.room.message','content':content})
    event=Event.parse_event({'type':'m.room.encrypted','event_id':ident,'sender':source.user,'origin_server_ts':101000,'content':encrypted,'unsigned':{}});event.room_id=ROOM
    decoded=target.decrypt_event(event)
    return event,decoded


class Response:
    def __init__(self,answer):self.content=self;self.answer=answer
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def raise_for_status(self):pass
    async def iter_chunked(self,n):
        yield json.dumps({'choices':[{'message':{'content':self.answer},'finish_reason':'stop'}]}).encode()


class HTTP:
    def __init__(self,answer):self.answer=answer;self.calls=[]
    def post(self,url,**kwargs):self.calls.append((url,kwargs));return Response(self.answer)


@pytest.mark.parametrize('domain,question,previous',[
    ('legal','Объясни отличие видов договоров.',''),
    ('finance','Чем вклад отличается от кредита?',''),
    ('legal','А как написать требование?','За работу не заплатили.'),
    ('finance','Что уточнить перед сравнением условий?','Хочу сравнить два предложения банка.'),
])
def test_real_megolm_preserves_model_packet_and_rich_answer(tmp_path,domain,question,previous):
    user,ug=native(tmp_path,USER,'ALICE');bot,bg=native(tmp_path,BOT,'BOT')
    wire,incoming=transfer(user,bot,{'msgtype':'m.text','body':question},'$question')
    assert incoming.body==question and bg.classify(ROOM,incoming) is None
    assert question not in json.dumps(wire.source,ensure_ascii=False)
    output='**Понятное объяснение**\n\nПервый абзац — с условием.\n\n1. Первый шаг.\n2. Второй шаг.\n\nЧто нужно уточнить?'
    plain,secure=HTTP(output),HTTP(output)
    mode=FreeReplyMode({ROOM:domain})
    async def generate():
        common=dict(endpoint='http://model.invalid/v1',model='synthetic-same-qwen-alias',api_key='synthetic',previous_question=previous,search_question=question)
        left=await mode.reply(plain,domain,question,None,**common)
        right=await mode.reply(secure,domain,incoming.body,None,**common)
        return left,right
    left,right=asyncio.run(generate())
    assert len(plain.calls)==len(secure.calls)==1
    assert plain.calls==secure.calls
    payload=plain.calls[0][1]['json']
    assert payload['temperature']==.3 and payload['max_tokens']==2200
    assert payload['messages']==free_messages(domain,question,[],previous)
    content=matrix_content(right);content.update({'org.sts.request_id':'$question','org.sts.reply_key':'stable-transaction'})
    encrypted,received=transfer(bot,user,content,'$reply')
    assert ug.classify(ROOM,received) is None
    assert left==right
    assert received.body==matrix_content(left)["body"]
    assert received.formatted_body==content['formatted_body']
    assert received.source['content']['org.sts.request_id']=='$question'
    assert 'Первый абзац' not in json.dumps(encrypted.source,ensure_ascii=False)
    asyncio.run(user.close());asyncio.run(bot.close())


def test_verified_chrome_excludes_unknown_sessions_without_trusting_them(tmp_path):
    user,_=native(tmp_path,USER,'ALICE');bot,_=native(tmp_path,BOT,'BOT')
    transfer(user,bot,{'msgtype':'m.text','body':'synthetic'},'$request')
    bot.rooms[ROOM].users[USER]=NS(invited=False)
    other=OlmDevice(USER,'OTHER',{'ed25519':'A'*43,'curve25519':'B'*43});bot.device_store.add(other);bot.store.save_device_keys({USER:{other.id:other}})
    assert verified_recipients_only(bot,bot.rooms[ROOM])==1
    assert bot.olm.is_device_blacklisted(other) and not bot.olm.is_device_verified(other)
    assert verified_recipients_only(bot,bot.rooms[ROOM])==0
    # Completing a later SAS uses the SDK's normal verify transition.
    bot.verify_device(other)
    assert bot.olm.is_device_verified(other) and not bot.olm.is_device_blacklisted(other)
    asyncio.run(user.close());asyncio.run(bot.close())


def test_unverified_only_recipient_defers_delivery_instead_of_finishing_it(tmp_path):
    bot,_=native(tmp_path,BOT,'BOT');bot.rooms[ROOM].users[USER]=NS(invited=False)
    with pytest.raises(CryptoPolicyError,match='no verified device'):
        verified_recipients_only(bot,bot.rooms[ROOM])
    asyncio.run(bot.close())
