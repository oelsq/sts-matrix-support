"""Production Matrix callback with synthetic people and no network I/O."""
import asyncio
import json
import time
from types import SimpleNamespace

from nio import RoomCreateResponse, RoomSendResponse


def test_bot_employee_selection_commands_and_archived_rooms_are_isolated(monkeypatch, tmp_path):
    import bot
    from human_handoff import HandoffStore

    output, created = [], []
    routes = tmp_path / 'routes.json'
    routes.write_text(json.dumps({'!finance:demo.local':'finance','!legal:demo.local':'legal'}))
    class Login: pass
    class Matrix:
        user = '@bot:demo.local'
        def __init__(self, *args, **kwargs): self.callback = None
        async def login(self, *args, **kwargs): return Login()
        def add_event_callback(self, callback, kind):
            if kind == bot.RoomMessageText: self.callback = callback
        async def room_create(self, **kwargs):
            created.append(kwargs)
            return RoomCreateResponse(f'!human{len(created)}:demo.local')
        async def room_send(self, **kwargs):
            output.append(kwargs)
            return RoomSendResponse('$sent', kwargs['room_id'])
        async def close(self): pass
        async def sync_forever(self, **kwargs):
            async def send(room, text, event, sender='@alice:demo.local'):
                await self.callback(SimpleNamespace(room_id=room),SimpleNamespace(sender=sender,
                    body=text,event_id=event,server_timestamp=int(time.time()*1000)+1000))
            await send('!finance:demo.local','!помощь','$help')
            await send('!finance:demo.local','!сотрудники','$list')
            assert created == []
            await send('!finance:demo.local','!финдеп2 Нужен расчёт','$choose')
            await send('!finance:demo.local','!другой Другая оценка','$change')
            await send('!finance:demo.local','!другой Другая оценка','$change')
            await send('!finance:demo.local','!финдеп8','$invalid')
            await send('!legal:demo.local','!юрдеп5 Проверить договор','$legal')
            before=len(output)
            await send('!human1:demo.local','Секретная беседа людей','$old')
            await send('!human2:demo.local','Ответ сотрудника','$staff',sender='@findep3:demo.local')
            assert len(output)==before
    monkeypatch.setattr(bot,'AsyncClient',Matrix)
    monkeypatch.setattr(bot,'LoginResponse',Login)
    monkeypatch.setattr(bot,'read_llm_key',lambda:'synthetic-only')
    for name in ['KNOWLEDGE_DIR','LLM_API_KEY_FILE']:
        monkeypatch.delenv(name,raising=False)
    env={'MATRIX_HOMESERVER':'http://homeserver:8008','MATRIX_USER':'@bot:demo.local',
         'MATRIX_PASSWORD':'synthetic','LLM_ENDPOINT':'http://litellm:4000/v1','LLM_MODEL':'synthetic',
         'MATRIX_ROOM_ROUTES_FILE':str(routes),'HANDOFF_DB':str(tmp_path/'handoff.sqlite3'),
         'ELEMENT_PUBLIC_URL':'http://localhost:18081','OFFLINE_MODE':'0',
         'LEGAL_DEPARTMENT_USERS':','.join('@jurdep'+(str(i) if i>1 else '')+':demo.local' for i in range(1,6)),
         'FINANCE_DEPARTMENT_USERS':','.join('@findep'+(str(i) if i>1 else '')+':demo.local' for i in range(1,6))}
    for name,value in env.items(): monkeypatch.setenv(name,value)
    asyncio.run(bot.main())
    assert len(created)==3
    assert created[0]['invite']==['@alice:demo.local','@findep2:demo.local']
    assert created[1]['invite']==['@alice:demo.local','@findep3:demo.local']
    assert created[2]['invite']==['@alice:demo.local','@jurdep5:demo.local']
    assert any('Выберите номер от 1 до 5' in item['content']['body'] for item in output)
    store=HandoffStore(tmp_path/'handoff.sqlite3')
    try:
        assert store.contains_room('!human1:demo.local')
        assert store.assignee('@alice:demo.local','finance')=='@findep3:demo.local'
        assert store.assignee('@alice:demo.local','legal')=='@jurdep5:demo.local'
    finally: store.db.close()
