"""Durable Matrix inbox, sync cursor and idempotent outbox for one bot process.

Credentials and pending user text remain in a private SQLite file. Completed
request bodies/outbox content are erased; event IDs remain for deduplication.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


class InboxStore:
    def __init__(self, filename, identity, *, clock=time.time, cipher=None):
        self.clock = clock
        self.identity, self.cipher = identity, cipher
        self.path = Path(filename)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path)+'.lock', os.O_CREAT|os.O_RDWR, 0o600)
        self.lock = os.fdopen(fd, 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError('Another bot process owns this inbox') from None
        fd = os.open(self.path, os.O_CREAT|os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS secure_pending (
          event_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox_requests (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
          room_id TEXT NOT NULL, sender TEXT NOT NULL, body TEXT NOT NULL,
          timestamp INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0, available REAL NOT NULL DEFAULT 0,
          completed REAL);
        CREATE TABLE IF NOT EXISTS inbox_outbox (
          event_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
          final INTEGER NOT NULL, sent_id TEXT, PRIMARY KEY(event_id,kind));
        CREATE TABLE IF NOT EXISTS answer_feedback (
          target_digest TEXT PRIMARY KEY, domain TEXT NOT NULL,
          category TEXT NOT NULL, day TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox_rooms (room_id TEXT PRIMARY KEY, anchor TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox_context (
          room_id TEXT, sender TEXT, domain TEXT, history TEXT, updated REAL,
          PRIMARY KEY(room_id,sender,domain));
        CREATE TABLE IF NOT EXISTS inbox_prepared_context (
          event_id TEXT, domain TEXT, effective TEXT, previous TEXT,
          PRIMARY KEY(event_id,domain));
        ''')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(inbox_requests)')}
        for name in ['received','started','finished','response_ready']:
            if name not in columns:self.db.execute('ALTER TABLE inbox_requests ADD COLUMN '+name+' REAL')
        if 'response_domain' not in columns:
            self.db.execute('ALTER TABLE inbox_requests ADD COLUMN response_domain TEXT')
        for name in ['delivery_attempts', 'delivery_deferrals', 'delivery_rate_limits']:
            if name not in columns:
                self.db.execute('ALTER TABLE inbox_requests ADD COLUMN '+name+' INTEGER NOT NULL DEFAULT 0')
        self.db.execute('CREATE INDEX IF NOT EXISTS inbox_pending ON inbox_requests(state,seq)')
        self.db.execute('CREATE INDEX IF NOT EXISTS inbox_thread ON inbox_requests(room_id,sender,seq,state)')
        self.db.execute("CREATE INDEX IF NOT EXISTS inbox_unfinished_thread ON inbox_requests(room_id,sender,seq) WHERE state!='done'")
        previous = self.meta('identity')
        if previous is not None and previous != identity:
            self.close()
            raise RuntimeError('Inbox belongs to another Matrix account/server')
        with self.db:
            self.set_meta('identity', identity)
            if self.meta('bootstrap_ms') is None:
                self.set_meta('bootstrap_ms', str(int(clock()*1000)))
            self.db.execute("UPDATE inbox_requests SET state='pending' WHERE state='processing'")
            from datetime import datetime, timezone
            cutoff=datetime.fromtimestamp(clock()-90*86400,timezone.utc).date().isoformat()
            self.db.execute('DELETE FROM answer_feedback WHERE day<?', (cutoff,))
        self.identity = identity
        self.bootstrap_ms = int(self.meta('bootstrap_ms'))
        try:self._configure_encryption()
        except Exception:self.close();raise

    def meta(self, key):
        row = self.db.execute('SELECT value FROM inbox_meta WHERE key=?', (key,)).fetchone()
        if row is None:return None
        return self._open('inbox_meta','value',key,row[0]) if key=='matrix_session' else row[0]

    def set_meta(self, key, value):
        self.db.execute('INSERT INTO inbox_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, self._seal('inbox_meta','value',key,value) if key=='matrix_session' else value))

    @property
    def cursor(self):
        return self.meta('sync_cursor')

    def enqueue(self, room_id, event):
        # Applied only against the persisted first-install cutoff, never restart time.
        if event.server_timestamp < self.bootstrap_ms:
            return False
        if not isinstance(event.body, str) or not event.event_id or not event.sender:
            return False
        result = self.db.execute('''INSERT OR IGNORE INTO inbox_requests
          (event_id,room_id,sender,body,timestamp,received) VALUES(?,?,?,?,?,?)''',
          (event.event_id, room_id, event.sender, self._seal('inbox_requests','body',event.event_id,event.body[:12000]), event.server_timestamp,self.clock()))
        return result.rowcount == 1

    def commit_sync(self, cursor, events, anchors, *, secure_events=None):
        with self.db:
            for room_id, event in events:
                if secure_events and event.event_id in secure_events:
                    self.defer_secure(room_id,event,secure_events[event.event_id])
                else:self.enqueue(room_id, event)
            for room_id, anchor in anchors.items():
                self.db.execute('INSERT INTO inbox_rooms VALUES(?,?) ON CONFLICT(room_id) DO UPDATE SET anchor=excluded.anchor', (room_id,anchor))
            self.set_meta('sync_cursor', cursor)

    def anchor(self, room_id):
        row = self.db.execute('SELECT anchor FROM inbox_rooms WHERE room_id=?', (room_id,)).fetchone()
        return row[0] if row else None

    def claim(self, *, delivery_blocked=False):
        with self.db:
            row = self.db.execute("""SELECT r.* FROM inbox_requests r WHERE r.state='pending' AND r.available<=?
                AND (?=0 OR NOT EXISTS (SELECT 1 FROM inbox_outbox o WHERE o.event_id=r.event_id AND o.final=1))
                AND NOT EXISTS (SELECT 1 FROM inbox_requests earlier WHERE earlier.room_id=r.room_id
                AND earlier.sender=r.sender AND earlier.seq<r.seq AND earlier.state!='done')
                ORDER BY r.seq LIMIT 1""", (self.clock(),int(delivery_blocked))).fetchone()
            if row:
                self.db.execute("UPDATE inbox_requests SET state='processing',attempts=attempts+1,started=COALESCE(started,?) WHERE seq=?", (self.clock(),row['seq']))
        if row is None:return None
        value=dict(row);value['body']=self._open('inbox_requests','body',value['event_id'],value['body']);return value

    def retry(self, event_id, attempts, *, delay=None):
        with self.db:
            self.db.execute("UPDATE inbox_requests SET state='pending',available=? WHERE event_id=?", (self.clock()+(max(.05,float(delay)) if delay is not None else min(60,2**min(attempts,6))),event_id))

    def reply_domain(self, event_id, domains):
        if not domains or not set(domains).issubset({'legal','finance','general'}):
            raise ValueError('Unknown response domain')
        domain=next(iter(domains)) if len(set(domains))==1 else 'mixed'
        with self.db:
            self.db.execute('UPDATE inbox_requests SET response_domain=? WHERE event_id=?', (domain,event_id))

    def rate_latest_answer(self, room, sender, category):
        if category not in {'helpful','incomplete','suspected_error','unhelpful'}:
            raise ValueError('Unknown feedback category')
        from datetime import datetime, timezone
        day=datetime.fromtimestamp(self.clock(),timezone.utc).date().isoformat()
        with self.db:
            row=self.db.execute("""SELECT r.event_id,r.response_domain FROM inbox_requests r
              WHERE r.room_id=? AND r.sender=? AND r.state='done' AND r.response_domain IS NOT NULL
              AND EXISTS (SELECT 1 FROM inbox_outbox o WHERE o.event_id=r.event_id AND o.final=1 AND o.sent_id IS NOT NULL)
              ORDER BY r.seq DESC LIMIT 1""", (room,sender)).fetchone()
            if row is None:return 'no_answer'
            key=self.transaction_id(row['event_id'],'feedback')
            old=self.db.execute('SELECT category FROM answer_feedback WHERE target_digest=?', (key,)).fetchone()
            if old and old[0]==category:return 'unchanged'
            self.db.execute('INSERT INTO answer_feedback VALUES(?,?,?,?) ON CONFLICT(target_digest) DO UPDATE SET category=excluded.category,day=excluded.day', (key,row['response_domain'],category,day))
            # Bound retention of pseudonymous one-vote markers to ninety days.
            cutoff=datetime.fromtimestamp(self.clock()-90*86400,timezone.utc).date().isoformat()
            self.db.execute('DELETE FROM answer_feedback WHERE day<?', (cutoff,))
        return 'updated' if old else 'recorded'

    def feedback_metrics(self):
        return {'status':'voluntary_user_feedback_not_expert_accuracy',
                'by_domain':{domain:{r[0]:r[1] for r in self.db.execute('SELECT category,count(*) FROM answer_feedback WHERE domain=? GROUP BY category',(domain,))}
                             for domain in ['legal','finance','general','mixed']},
                'question_or_answer_text_stored':False}

    def delivery_attempt(self, event_id):
        with self.db:
            self.db.execute('UPDATE inbox_requests SET delivery_attempts=delivery_attempts+1 WHERE event_id=?', (event_id,))

    def delivery_deferred(self, event_id, *, server_rate_limit=False):
        with self.db:
            self.db.execute('UPDATE inbox_requests SET delivery_deferrals=delivery_deferrals+1,delivery_rate_limits=delivery_rate_limits+? WHERE event_id=?', (int(server_rate_limit),event_id))

    def pending(self):
        return self.db.execute("SELECT count(*) FROM inbox_requests WHERE state!='done'").fetchone()[0]

    def metrics(self):
        """Aggregate queue state only; never expose message text or Matrix identities."""
        states={r[0]:r[1] for r in self.db.execute('SELECT state,count(*) FROM inbox_requests GROUP BY state')}
        pending=self.db.execute("SELECT min(received) FROM inbox_requests WHERE state!='done'").fetchone()[0]
        return {'states':states,'oldest_pending_seconds':max(0,self.clock()-pending) if pending else 0}

    def finish(self, event_id):
        with self.db:
            self.db.execute("UPDATE inbox_requests SET state='done',body='',completed=?,finished=? WHERE event_id=?", (self.clock(),self.clock(),event_id))
            self.db.execute("UPDATE inbox_outbox SET content='' WHERE event_id=?", (event_id,))
            self.db.execute("DELETE FROM inbox_prepared_context WHERE event_id=?", (event_id,))

    def final_ready(self, event_id):
        return self.db.execute('SELECT 1 FROM inbox_outbox WHERE event_id=? AND final=1', (event_id,)).fetchone() is not None

    def prepare_reply(self, event_id, kind, content, final):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO inbox_outbox(event_id,kind,content,final) VALUES(?,?,?,?)',
                            (event_id,kind,self._seal('inbox_outbox','content',event_id+'\0'+kind,json.dumps(content,ensure_ascii=False)),int(final)))
        if final:
            with self.db:self.db.execute('UPDATE inbox_requests SET response_ready=COALESCE(response_ready,?) WHERE event_id=?',(self.clock(),event_id))
        return self._reply_row(self.db.execute('SELECT * FROM inbox_outbox WHERE event_id=? AND kind=?', (event_id,kind)).fetchone())

    def replies(self, event_id):
        return [self._reply_row(r) for r in self.db.execute('SELECT * FROM inbox_outbox WHERE event_id=? ORDER BY final,kind', (event_id,))]

    def sent(self, event_id, kind, sent_id):
        with self.db:
            self.db.execute('UPDATE inbox_outbox SET sent_id=? WHERE event_id=? AND kind=?', (sent_id,event_id,kind))

    def transaction_id(self, event_id, kind):
        return hashlib.sha256((self.identity+'\0'+event_id+'\0'+kind).encode()).hexdigest()

    def load_history(self, room, sender, domain, ttl):
        row=self.db.execute('SELECT history,updated FROM inbox_context WHERE room_id=? AND sender=? AND domain=?',(room,sender,domain)).fetchone()
        return json.loads(self._open('inbox_context','history','\0'.join((room,sender,domain)),row[0])) if row and self.clock()-row[1]<ttl else []

    def save_history(self, room, sender, domain, history, *, request_id=None, context=None):
        with self.db:
            self.db.execute('DELETE FROM inbox_context WHERE updated<?',(self.clock()-1800,))
            self.db.execute('INSERT INTO inbox_context VALUES(?,?,?,?,?) ON CONFLICT(room_id,sender,domain) DO UPDATE SET history=excluded.history,updated=excluded.updated',(room,sender,domain,self._seal('inbox_context','history','\0'.join((room,sender,domain)),json.dumps(history,ensure_ascii=False)),self.clock()))
            self.db.execute('DELETE FROM inbox_context WHERE rowid IN (SELECT rowid FROM inbox_context ORDER BY updated DESC,rowid DESC LIMIT -1 OFFSET 1000)')
            if request_id is not None:self.save_context(request_id,domain,*context)

    def prepared_context(self, event_id, domain):
        row=self.db.execute('SELECT effective,previous FROM inbox_prepared_context WHERE event_id=? AND domain=?',(event_id,domain)).fetchone()
        if row is None:return None
        return tuple(self._open('inbox_prepared_context',field,event_id+'\0'+domain,value) for field,value in zip(('effective','previous'),row))

    def save_context(self, event_id, domain, effective, previous):
        self.db.execute('INSERT OR IGNORE INTO inbox_prepared_context VALUES(?,?,?,?)',(event_id,domain,self._seal('inbox_prepared_context','effective',event_id+'\0'+domain,effective),self._seal('inbox_prepared_context','previous',event_id+'\0'+domain,previous)))

    def clear_history(self, room, sender):
        with self.db:self.db.execute('DELETE FROM inbox_context WHERE room_id=? AND sender=?',(room,sender))


    def _purpose(self, table, field, row_key):
        return '\0'.join((self.identity,table,field,row_key))

    def _seal(self, table, field, row_key, text):
        return self.cipher.seal(text,self._purpose(table,field,row_key)) if self.cipher else text

    def _open(self, table, field, row_key, text):
        return self.cipher.open(text,self._purpose(table,field,row_key)) if self.cipher else text

    def _reply_row(self, row):
        value=dict(row)
        value['content']=self._open('inbox_outbox','content',value['event_id']+'\0'+value['kind'],value['content'])
        return value

    def _configure_encryption(self):
        from state_cipher import StateEncryptionError
        row=self.db.execute("SELECT value FROM inbox_meta WHERE key='state_key_id'").fetchone()
        if row:
            if self.cipher is None or row[0]!=self.cipher.key_id:
                raise StateEncryptionError('Inbox encryption key is missing or changed')
            return
        if self.cipher is None:return
        fields=[('inbox_requests',('event_id',),('body',)),
                ('inbox_outbox',('event_id','kind'),('content',)),
                ('inbox_context',('room_id','sender','domain'),('history',)),
                ('inbox_prepared_context',('event_id','domain'),('effective','previous')),
                ('secure_pending',('event_id',),('payload',))]
        with self.db:
            for table,keys,columns in fields:
                for data in self.db.execute('SELECT '+','.join((*keys,*columns))+' FROM '+table).fetchall():
                    ident='\0'.join(str(data[k]) for k in keys)
                    for field in columns:
                        self.db.execute('UPDATE '+table+' SET '+field+'=? WHERE '+' AND '.join(k+'=?' for k in keys),
                                        (self._seal(table,field,ident,data[field]),*[data[k] for k in keys]))
            old=self.db.execute("SELECT value FROM inbox_meta WHERE key='matrix_session'").fetchone()
            if old:self.db.execute("UPDATE inbox_meta SET value=? WHERE key='matrix_session'",(self._seal('inbox_meta','value','matrix_session',old[0]),))
            self.db.execute("INSERT INTO inbox_meta VALUES('state_key_id',?)",(self.cipher.key_id,))
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.db.execute('VACUUM')
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def defer_secure(self, room, event, payload):
        if event.server_timestamp<self.bootstrap_ms:return False
        if self.cipher is None:
            from state_cipher import StateEncryptionError
            raise StateEncryptionError('Deferred E2EE requires encrypted storage')
        row=self.db.execute("INSERT OR IGNORE INTO inbox_requests(event_id,room_id,sender,body,timestamp,received,state) VALUES(?,?,?,'',?,?,'waiting_key')",(event.event_id,room,event.sender,event.server_timestamp,self.clock()))
        if row.rowcount:
            value=self._seal('secure_pending','payload',event.event_id,json.dumps(payload,ensure_ascii=False))
            self.db.execute('INSERT INTO secure_pending VALUES(?,?)',(event.event_id,value))
        return bool(row.rowcount)

    def secure_pending(self, limit=100):
        if not 1<=limit<=500:raise ValueError('Invalid encrypted retry limit')
        rows=self.db.execute("SELECT r.event_id,r.room_id,r.sender,p.payload FROM inbox_requests r JOIN secure_pending p ON r.event_id=p.event_id WHERE r.state='waiting_key' ORDER BY r.seq LIMIT ?",(limit,)).fetchall()
        return [dict(row,payload=json.loads(self._open('secure_pending','payload',row['event_id'],row['payload']))) for row in rows]

    def resolve_secure(self, room, event):
        with self.db:
            row=self.db.execute("SELECT room_id,sender FROM inbox_requests WHERE event_id=? AND state='waiting_key'",(event.event_id,)).fetchone()
            if row is None:return False
            if (row['room_id'],row['sender'])!=(room,event.sender):raise ValueError('Encrypted event identity changed')
            self.db.execute("UPDATE inbox_requests SET body=?,state='pending' WHERE event_id=?",(self._seal('inbox_requests','body',event.event_id,event.body[:12000]),event.event_id))
            self.db.execute('DELETE FROM secure_pending WHERE event_id=?',(event.event_id,))
        return True

    def close(self):
        if getattr(self,'db',None):self.db.close();self.db=None
        if getattr(self,'lock',None):self.lock.close();self.lock=None
