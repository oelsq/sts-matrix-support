import base64
import importlib.util
from pathlib import Path


def test_setup_creates_private_persistent_state_key_without_starting_bot(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('crypto_test_setup',root/'scripts/setup.py')
    setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
    (tmp_path/'.env.example').write_text((root/'.env.example').read_text())
    monkeypatch.setattr(setup,'ROOT',tmp_path);monkeypatch.setattr(setup,'RUNTIME',tmp_path/'runtime');monkeypatch.setattr(setup,'ENV',tmp_path/'.env')
    calls=[];monkeypatch.setattr(setup,'compose',lambda *args:calls.append(args))
    setup.initialize();key=tmp_path/'runtime/secrets/state_key'
    original=key.read_bytes();assert len(base64.urlsafe_b64decode(original))==32
    assert key.stat().st_mode&0o777==0o600
    assert (tmp_path/'runtime/bot').stat().st_mode&0o777==0o700
    setup.initialize();assert key.read_bytes()==original
    assert all('bot' not in call for call in calls)


def test_second_process_cannot_migrate_handoff_before_inbox_lock(tmp_path,monkeypatch):
    import asyncio,hashlib
    import pytest
    import bot
    from matrix_inbox import InboxStore
    host='http://synthetic.invalid';user='@bot:test'
    identity=hashlib.sha256((host+'\0'+user).encode()).hexdigest()
    store=InboxStore(tmp_path/'matrix_inbox.sqlite3',identity)
    for name,value in {'MATRIX_HOMESERVER':host,'MATRIX_USER':user,'MATRIX_PASSWORD':'synthetic','LLM_ENDPOINT':'http://model.invalid','LLM_MODEL':'synthetic','HANDOFF_DB':str(tmp_path/'handoff.sqlite3'),'MATRIX_INBOX_DB':str(tmp_path/'matrix_inbox.sqlite3'),'MATRIX_ROOM_ROUTES_FILE':str(tmp_path/'routes.json'),'OFFLINE_MODE':'0'}.items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('KNOWLEDGE_DIR',raising=False)
    monkeypatch.delenv('MATRIX_E2EE_ENABLED',raising=False)
    monkeypatch.delenv('STATE_ENCRYPTION_KEY_FILE',raising=False)
    monkeypatch.setattr(bot,'read_llm_key',lambda:'synthetic')
    monkeypatch.setattr(bot,'AsyncClient',lambda *a,**kw:object())
    monkeypatch.setattr(bot,'department_roster',lambda env:{'legal':('@staff:test',)})
    def forbidden(*a,**kw):raise AssertionError('Handoff must not be opened before the exclusive inbox lock')
    monkeypatch.setattr(bot,'HandoffStore',forbidden)
    try:
        with pytest.raises(RuntimeError,match='Another bot process'):asyncio.run(bot.main())
    finally:store.close()
