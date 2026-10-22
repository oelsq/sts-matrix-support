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
        CREATE TABLE IF NOT EXISTS inbox_rooms (room_id TEXT PRIMARY KEY, anchor TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox_context (
          room_id TEXT, sender TEXT, domain TEXT, history TEXT, updated REAL,
          PRIMARY KEY(room_id,sender,domain));
        CREATE TABLE IF NOT EXISTS inbox_prepared_context (
          event_id TEXT, domain TEXT, effective TEXT, previous TEXT,
          PRIMARY KEY(event_id,domain));
        ''')
        previous = self.meta('identity')
        if previous is not None and previous != identity:
            self.close()
            raise RuntimeError('Inbox belongs to another Matrix account/server')
        with self.db:
            self.set_meta('identity', identity)
            if self.meta('bootstrap_ms') is None:
                self.set_meta('bootstrap_ms', str(int(clock()*1000)))
            self.db.execute("UPDATE inbox_requests SET state='pending' WHERE state='processing'")
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
          (event_id,room_id,sender,body,timestamp) VALUES(?,?,?,?,?)''',
          (event.event_id, room_id, event.sender, event.body[:12000], event.server_timestamp))
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

    def claim(self):
        with self.db:
            row = self.db.execute("SELECT * FROM inbox_requests WHERE state='pending' AND available<=? ORDER BY seq LIMIT 1", (self.clock(),)).fetchone()
            if row:
                self.db.execute("UPDATE inbox_requests SET state='processing',attempts=attempts+1 WHERE seq=?", (row['seq'],))
        return dict(row) if row else None

    def retry(self, event_id, attempts):
        with self.db:
            self.db.execute("UPDATE inbox_requests SET state='pending',available=? WHERE event_id=?", (self.clock()+min(60,2**min(attempts,6)),event_id))

    def pending(self):
        return self.db.execute("SELECT count(*) FROM inbox_requests WHERE state!='done'").fetchone()[0]

    def finish(self, event_id):
        with self.db:
            self.db.execute("UPDATE inbox_requests SET state='done',body='',completed=? WHERE event_id=?", (self.clock(),event_id))
            self.db.execute("UPDATE inbox_outbox SET content='' WHERE event_id=?", (event_id,))
            self.db.execute("DELETE FROM inbox_prepared_context WHERE event_id=?", (event_id,))

    def final_ready(self, event_id):
        return self.db.execute('SELECT 1 FROM inbox_outbox WHERE event_id=? AND final=1', (event_id,)).fetchone() is not None

    def prepare_reply(self, event_id, kind, content, final):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO inbox_outbox(event_id,kind,content,final) VALUES(?,?,?,?)',
                            (event_id,kind,json.dumps(content,ensure_ascii=False),int(final)))
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
