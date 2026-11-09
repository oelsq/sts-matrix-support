import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from nio import RoomCreateResponse
from matrix_inbox import InboxStore
from private_rooms import PrivateRooms
from state_cipher import StateCipher
from human_handoff import HandoffStore,HumanHandoff


def test_each_user_has_two_isolated_rooms_and_persistent_routes(tmp_path):
    cipher=StateCipher(b'K'*32);store=InboxStore(tmp_path/'inbox','test',cipher=cipher)
    client=NS(user='@bot:test',rooms={},room_create=AsyncMock(side_effect=[RoomCreateResponse('!a:test'),RoomCreateResponse('!b:test'),RoomCreateResponse('!c:test')]))
    fixed={};rooms=PrivateRooms(store,client,fixed,None,'http://localhost',cipher)
    async def run():
        assert await rooms.ensure('@alice:test','legal')=='!a:test'
        assert await rooms.ensure('@alice:test','finance')=='!b:test'
        assert await rooms.ensure('@bob:test','legal')=='!c:test'
        assert await rooms.ensure('@alice:test','legal')=='!a:test'
    asyncio.run(run());assert client.room_create.await_count==3
    args=client.room_create.await_args_list[0].kwargs
    import inspect
    from nio import AsyncClient
    inspect.signature(AsyncClient.room_create).bind(client,**args)
    assert args['invite']==['@alice:test'] and not args['federate']
    assert args['power_level_override']['invite']==100
    assert 'alice' not in args['alias']
    assert {e['type'] for e in args['initial_state']}=={'m.room.encryption','m.room.history_visibility','m.room.guest_access'}
    client.rooms['!a:test']=NS(encrypted=True,users={'@bot:test':None,'@alice:test':None})
    assert rooms.permitted('!a:test','@alice:test')
    assert not rooms.permitted('!a:test','@bob:test')
    client.rooms['!a:test'].users['@mallory:test']=None
    assert not rooms.permitted('!a:test','@alice:test')
    store.close();reopened=InboxStore(tmp_path/'inbox','test',cipher=cipher)
    restored=PrivateRooms(reopened,client,{},None,'http://localhost',cipher)
    assert restored.required_encryption('!a:test') and restored.known_peer('@alice:test')
    reopened.close()


def test_secure_handoff_never_sends_until_both_people_join_and_devices_are_verified(tmp_path):
    from nio import RoomSendResponse,RoomLeaveResponse
    from nio.exceptions import OlmUnverifiedDeviceError
    cipher=StateCipher(b'K'*32);store=HandoffStore(tmp_path/'handoff',cipher=cipher)
    client=NS(user='@bot:test',rooms={},room_create=AsyncMock(return_value=RoomCreateResponse('!h:test')),room_send=AsyncMock(),room_leave=AsyncMock(return_value=RoomLeaveResponse()))
    handoff=HumanHandoff(client,store,{'legal':['@staff:test']},'http://localhost',secure=True)
    async def run():
        await handoff.open('@alice:test','legal',('private question','private answer'),request_id='$handoff')
        client.room_send.assert_not_awaited()
        client.rooms['!h:test']=NS(encrypted=True,users={'@bot:test':None,'@alice:test':None})
        await handoff.flush_secure();client.room_send.assert_not_awaited()
        client.rooms['!h:test'].users['@staff:test']=None
        client.room_send.side_effect=OlmUnverifiedDeviceError('unverified')
        await handoff.flush_secure();client.room_leave.assert_not_awaited()
        first=client.room_send.call_args.kwargs
        assert first['ignore_unverified_devices'] is False
        client.room_send.side_effect=None;client.room_send.return_value=RoomSendResponse('$sent','!h:test')
        await handoff.flush_secure()
        assert client.room_send.call_args.kwargs['tx_id']==first['tx_id']
        client.room_leave.assert_awaited_once()
        assert store.get('@alice:test','legal')==('!h:test',1,1)
        await handoff.flush_secure();assert client.room_send.await_count==2
    asyncio.run(run());store.db.close()


def test_archival_does_not_extend_sensitive_context_retention(tmp_path):
    now=[100];store=HandoffStore(tmp_path/'handoff',cipher=StateCipher(b'K'*32),clock=lambda:now[0],retention_days=1)
    store.create('@alice:test','legal','!old:test','@staff1:test',context=['private','answer'])
    now[0]+=80000
    store.create('@alice:test','legal','!new:test','@staff2:test',context=['new','answer'])
    now[0]+=6401;store.expire_context()
    assert store.room_record('!old:test')['context_json'] is None
    assert store.room_record('!new:test')['context_json'] is not None
    store.db.close()
