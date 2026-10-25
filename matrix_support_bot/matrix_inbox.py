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
    def __init__(self, filename, identity, *, clock=time.time):
        self.clock = clock
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
        self.db.executescript('''
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

    def meta(self, key):
        row = self.db.execute('SELECT value FROM inbox_meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.db.execute('INSERT INTO inbox_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

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
          (event.event_id, room_id, event.sender, event.body[:12000], event.server_timestamp,self.clock()))
        return result.rowcount == 1

    def commit_sync(self, cursor, events, anchors):
        with self.db:
            for room_id, event in events:
                self.enqueue(room_id, event)
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
        return dict(row) if row else None

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
                            (event_id,kind,json.dumps(content,ensure_ascii=False),int(final)))
        if final:
            with self.db:self.db.execute('UPDATE inbox_requests SET response_ready=COALESCE(response_ready,?) WHERE event_id=?',(self.clock(),event_id))
        return dict(self.db.execute('SELECT * FROM inbox_outbox WHERE event_id=? AND kind=?', (event_id,kind)).fetchone())

    def replies(self, event_id):
        return [dict(r) for r in self.db.execute('SELECT * FROM inbox_outbox WHERE event_id=? ORDER BY final,kind', (event_id,))]

    def sent(self, event_id, kind, sent_id):
        with self.db:
            self.db.execute('UPDATE inbox_outbox SET sent_id=? WHERE event_id=? AND kind=?', (sent_id,event_id,kind))

    def transaction_id(self, event_id, kind):
        return hashlib.sha256((self.identity+'\0'+event_id+'\0'+kind).encode()).hexdigest()

    def load_history(self, room, sender, domain, ttl):
        row=self.db.execute('SELECT history,updated FROM inbox_context WHERE room_id=? AND sender=? AND domain=?',(room,sender,domain)).fetchone()
        return json.loads(row[0]) if row and self.clock()-row[1]<ttl else []

    def save_history(self, room, sender, domain, history, *, request_id=None, context=None):
        with self.db:
            self.db.execute('DELETE FROM inbox_context WHERE updated<?',(self.clock()-1800,))
            self.db.execute('INSERT INTO inbox_context VALUES(?,?,?,?,?) ON CONFLICT(room_id,sender,domain) DO UPDATE SET history=excluded.history,updated=excluded.updated',(room,sender,domain,json.dumps(history,ensure_ascii=False),self.clock()))
            self.db.execute('DELETE FROM inbox_context WHERE rowid IN (SELECT rowid FROM inbox_context ORDER BY updated DESC,rowid DESC LIMIT -1 OFFSET 1000)')
            if request_id is not None:self.save_context(request_id,domain,*context)

    def prepared_context(self, event_id, domain):
        row=self.db.execute('SELECT effective,previous FROM inbox_prepared_context WHERE event_id=? AND domain=?',(event_id,domain)).fetchone()
        return tuple(row) if row else None

    def save_context(self, event_id, domain, effective, previous):
        self.db.execute('INSERT OR IGNORE INTO inbox_prepared_context VALUES(?,?,?,?)',(event_id,domain,effective,previous))

    def clear_history(self, room, sender):
        with self.db:self.db.execute('DELETE FROM inbox_context WHERE room_id=? AND sender=?',(room,sender))

    def close(self):
        if getattr(self,'db',None):self.db.close();self.db=None
        if getattr(self,'lock',None):self.lock.close();self.lock=None
