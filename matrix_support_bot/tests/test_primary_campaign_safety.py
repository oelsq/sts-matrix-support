"""Primary-room testing must never fall back to reading another user's reply."""
import importlib.util,sqlite3
from pathlib import Path
import pytest

ROOT=Path(__file__).parents[2]
if not (ROOT/'scripts/main_chat_campaign.py').is_file():
    pytest.skip('Repository-only opt-in campaign tooling',allow_module_level=True)
spec=importlib.util.spec_from_file_location('primary_campaign',ROOT/'scripts/main_chat_campaign.py')
campaign=importlib.util.module_from_spec(spec);spec.loader.exec_module(campaign)

@pytest.fixture
def db(tmp_path):
    path=tmp_path/'inbox.sqlite3';w=sqlite3.connect(path)
    w.executescript('''CREATE TABLE inbox_requests(event_id,sender,room_id,state,received,started,response_ready,finished,delivery_attempts,delivery_deferrals,delivery_rate_limits);
    CREATE TABLE inbox_outbox(event_id,final,sent_id);''')
    w.execute('INSERT INTO inbox_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',('$ours','@sts-main-cases-test:demo.local','!legal:demo.local','done',1,2,3,4,1,0,0))
    w.execute('INSERT INTO inbox_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',('$victim','@real-user:demo.local','!legal:demo.local','done',1,2,3,4,1,0,0))
    w.executemany('INSERT INTO inbox_outbox VALUES(?,?,?)',[('$ours',0,'$progress'),('$ours',1,'$ours-reply'),('$victim',1,'$private')]);w.commit();w.close()
    r=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);r.row_factory=sqlite3.Row
    yield r
    r.close()

def test_only_correlated_final_reply_is_selected(db):
    result=campaign.own_reply_row(db,'$ours','@sts-main-cases-test:demo.local','!legal:demo.local')
    assert result['sent_id']=='$ours-reply'

def test_no_fallback_to_some_other_users_event(db):
    assert campaign.own_reply_row(db,'$victim','@sts-main-cases-test:demo.local','!legal:demo.local') is None

def test_wrong_room_never_selects_another_reply(db):
    assert campaign.own_reply_row(db,'$ours','@sts-main-cases-test:demo.local','!other:demo.local') is None

@pytest.mark.parametrize('sender,event',[('@real-user:demo.local','$victim'),('@sts-main-cases-test:demo.local','not-event')])
def test_unscoped_identity_or_event_is_rejected(db,sender,event):
    with pytest.raises(ValueError):campaign.own_reply_row(db,event,sender,'!legal:demo.local')

def test_request_id_is_a_bound_parameter_and_database_remains_read_only(db):
    assert campaign.own_reply_row(db,"$x' OR 1=1 --",'@sts-main-cases-test:demo.local','!legal:demo.local') is None
    with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM inbox_outbox')
