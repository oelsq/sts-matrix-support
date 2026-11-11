"""Explicit handoff and employee changes. The bot never reads human conversations."""
import asyncio
import html
import json
import os
import time
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from nio import RoomCreateResponse, RoomLeaveResponse, RoomPreset, RoomSendResponse

LABELS = {"legal": "ЮрДеп", "finance": "ФинДеп"}
COLUMNS = ('user', 'domain', 'room', 'prepared', 'departed', 'assignee', 'context_json', 'note')


class HandoffChoiceError(ValueError):
    """A selection the user can correct without creating a room."""


def department_roster(environ):
    teams = {}
    for domain, prefix in [('legal', 'LEGAL'), ('finance', 'FINANCE')]:
        raw = environ.get(prefix + '_DEPARTMENT_USERS') or environ.get(prefix + '_DEPARTMENT_USER', '')
        users = tuple(x.strip() for x in raw.split(',') if x.strip())
        if any(not re.fullmatch(r'@[^\s:,]+:[^\s,]+', x) for x in users):
            raise ValueError('Invalid department Matrix user')
        if len(set(users)) != len(users):
            raise ValueError('Duplicate employee in department roster')
        if users:
            teams[domain] = users
    return teams


def handoff_command(text):
    """Only explicit commands authorize copying this user's initial AI context."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    command = parts[0].casefold()
    commands = {'!человек': 'auto', '!юрдеп': 'legal', '!финдеп': 'finance',
                '!другой': 'auto', '!сотрудники': 'auto'}
    numbered = re.fullmatch(r'!(юрдеп|финдеп)(\d+)', command)
    if numbered:
        return ('legal' if numbered[1] == 'юрдеп' else 'finance'), parts[1] if len(parts) > 1 else ''
    if command not in commands:
        return None
    return commands[command], parts[1] if len(parts) > 1 else ''


def handoff_selection(text):
    command = text.strip().split(maxsplit=1)[0].casefold()
    numbered = re.fullmatch(r'!(?:юрдеп|финдеп)(\d+)', command)
    return (int(numbered[1]) if numbered else None), command == '!другой'


def choose_department(requested, fixed_domain, previous_domains):
    if requested in LABELS:
        return requested
    if fixed_domain in LABELS:
        return fixed_domain
    if len(previous_domains) == 1 and previous_domains[0] in LABELS:
        return previous_domains[0]
    return None


def context_message(user, context, note):
    question, answer = context or ('', '')
    lines = ['Обращение к живому сотруднику от ' + user + '.',
             'Пользователь запросил передачу командой. Ответ ИИ ниже может быть ошибочным.']
    if question:
        lines.extend(['\nПоследний вопрос пользователя:', question[:12000]])
    if answer:
        lines.extend(['\nПредыдущий ответ ИИ (для проверки сотрудником):', answer[:14000]])
    if note:
        lines.extend(['\nКомментарий пользователя:', note[:12000]])
    if not question and not note:
        lines.append('Пожалуйста, опишите свой вопрос в этой комнате.')
    lines.append('\nДальше здесь общаются люди. Бот выходит и не отвечает в этой комнате.')
    lines.append('Чтобы выбрать другого сотрудника, вернитесь в чат ИИ: !сотрудники — список, !другой — сменить. '
                 'Переписка с предыдущим сотрудником автоматически не копируется.')
    return '\n'.join(lines)


class HandoffStore:
    def __init__(self, path, *, cipher=None, retention_days=30, clock=time.time):
        self.cipher,self.clock=cipher,clock
        if not 1<=retention_days<=365:raise ValueError('Invalid handoff retention')
        self.retention_days=retention_days
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if str(path)!=':memory:':
            fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd);os.chmod(path,0o600)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('''CREATE TABLE IF NOT EXISTS handoffs (
            user TEXT NOT NULL, domain TEXT NOT NULL, room TEXT NOT NULL UNIQUE,
            prepared INTEGER NOT NULL DEFAULT 0, departed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user, domain))''')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(handoffs)')}
        for name, sql_type in [('assignee', 'TEXT'), ('context_json', 'TEXT'), ('note', 'TEXT')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE handoffs ADD COLUMN {name} {sql_type}')
        self.db.execute('''CREATE TABLE IF NOT EXISTS handoff_archive (
            user TEXT NOT NULL, domain TEXT NOT NULL, room TEXT PRIMARY KEY,
            prepared INTEGER NOT NULL, departed INTEGER NOT NULL, assignee TEXT,
            context_json TEXT, note TEXT)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS department_rotation (domain TEXT PRIMARY KEY, next_position INTEGER NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS handoff_requests (
            request_id TEXT PRIMARY KEY, user TEXT NOT NULL, domain TEXT NOT NULL, room TEXT NOT NULL)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS handoff_security (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        for table in ['handoffs','handoff_archive']:
            columns={row[1] for row in self.db.execute('PRAGMA table_info('+table+')')}
            if 'context_created' not in columns:
                self.db.execute('ALTER TABLE '+table+' ADD COLUMN context_created REAL')
                self.db.execute('UPDATE '+table+' SET context_created=?',(self.clock(),))
        self.db.commit()
        try:self._configure_encryption()
        except Exception:self.db.close();raise
        self.expire_context()

    def record(self, user, domain):
        row = self.db.execute('SELECT '+','.join(COLUMNS)+' FROM handoffs WHERE user=? AND domain=?', (user, domain)).fetchone()
        return self._record('handoffs',row) if row else None

    def room_record(self, room):
        for table in ('handoffs', 'handoff_archive'):
            row = self.db.execute('SELECT '+','.join(COLUMNS)+f' FROM {table} WHERE room=?', (room,)).fetchone()
            if row:
                return self._record(table,row)
        return None

    def request_record(self, request_id, user, domain):
        if not request_id:
            return None
        row = self.db.execute('SELECT user,domain,room FROM handoff_requests WHERE request_id=?', (request_id,)).fetchone()
        if row and row[:2] != (user, domain):
            raise ValueError('Handoff request belongs to another user or domain')
        return self.room_record(row[2]) if row else None

    def remember(self, request_id, user, domain, room):
        if request_id:
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO handoff_requests VALUES(?,?,?,?)', (request_id, user, domain, room))

    def assignee(self, user, domain):
        record = self.record(user, domain)
        return record['assignee'] if record else None

    def next_employee(self, domain, roster):
        row = self.db.execute('SELECT next_position FROM department_rotation WHERE domain=?', (domain,)).fetchone()
        position = (row[0] if row else 0) % len(roster)
        return roster[position], (position + 1) % len(roster)

    def backfill_legacy(self, domain, employee):
        with self.db:
            self.db.execute('UPDATE handoffs SET assignee=? WHERE domain=? AND assignee IS NULL', (employee, domain))

    def get(self, user, domain):
        r = self.record(user, domain)
        return (r['room'], r['prepared'], r['departed']) if r else None

    def create(self, user, domain, room, assignee=None, next_position=None, *, context=None, note='', request_id=None, encrypted=False):
        with self.db:
            old = self.record(user, domain)
            if old:
                self.db.execute('INSERT INTO handoff_archive('+','.join(COLUMNS)+',context_created) VALUES(?,?,?,?,?,?,?,?,?)',(*self._encoded_record('handoff_archive',old),self.db.execute('SELECT context_created FROM handoffs WHERE room=?',(old['room'],)).fetchone()[0]))
                self.db.execute('DELETE FROM handoffs WHERE user=? AND domain=?', (user, domain))
            self.db.execute('INSERT INTO handoffs(user,domain,room,assignee,context_json,note) VALUES(?,?,?,?,?,?)',
                            (user, domain, room, assignee, self._seal('handoffs','context_json',room,json.dumps(context, ensure_ascii=False)), self._seal('handoffs','note',room,note[:12000])))
            self.db.execute('UPDATE handoffs SET context_created=? WHERE room=?',(self.clock(),room))
            if encrypted:self.db.execute("INSERT OR REPLACE INTO handoff_security VALUES(?,?)",('encrypted:'+room,'yes'))
            if next_position is not None:
                self.db.execute('INSERT INTO department_rotation(domain,next_position) VALUES(?,?) ON CONFLICT(domain) DO UPDATE SET next_position=excluded.next_position', (domain, next_position))
            if request_id:
                self.db.execute('INSERT INTO handoff_requests VALUES(?,?,?,?)', (request_id, user, domain, room))

    def mark_room(self, room, column):
        if column not in {'prepared', 'departed'}:
            raise ValueError('Invalid status')
        with self.db:
            for table in ('handoffs', 'handoff_archive'):
                self.db.execute(f'UPDATE {table} SET {column}=1 WHERE room=?', (room,))

    def mark(self, user, domain, column):
        self.mark_room(self.record(user, domain)['room'], column)

    def contains_room(self, room):
        return self.room_record(room) is not None

    def for_room(self, room):
        r = self.room_record(room)
        return (r['user'], r['domain'], r['prepared'], r['departed']) if r else None


    def _seal(self, table, field, room, text):
        if text is None:return None
        return self.cipher.seal(text,'\0'.join(('handoff',table,room,field))) if self.cipher else text

    def _open(self, table, field, room, text):
        if text is None:return None
        return self.cipher.open(text,'\0'.join(('handoff',table,room,field))) if self.cipher else text

    def _record(self, table, row):
        value=dict(zip(COLUMNS,row))
        for field in ['context_json','note']:value[field]=self._open(table,field,value['room'],value[field])
        return value

    def _encoded_record(self, table, value):
        return tuple(self._seal(table,k,value['room'],value[k]) if k in {'context_json','note'} else value[k] for k in COLUMNS)

    def _configure_encryption(self):
        from state_cipher import StateEncryptionError
        old=self.db.execute("SELECT value FROM handoff_security WHERE key='state_key_id'").fetchone()
        if old:
            if self.cipher is None or old[0]!=self.cipher.key_id:
                raise StateEncryptionError('Handoff encryption key is missing or changed')
            return
        if self.cipher is None:return
        with self.db:
            for table in ['handoffs','handoff_archive']:
                for room,context,note in self.db.execute('SELECT room,context_json,note FROM '+table).fetchall():
                    self.db.execute('UPDATE '+table+' SET context_json=?,note=? WHERE room=?',(self._seal(table,'context_json',room,context),self._seal(table,'note',room,note),room))
            self.db.execute("INSERT INTO handoff_security VALUES('state_key_id',?)",(self.cipher.key_id,))
        self.db.execute('VACUUM')

    def encrypted_room(self, room):
        return self.db.execute("SELECT 1 FROM handoff_security WHERE key=? AND value='yes'",('encrypted:'+room,)).fetchone() is not None

    def expire_context(self):
        cutoff=self.clock()-self.retention_days*86400
        with self.db:
            for table in ['handoffs','handoff_archive']:
                self.db.execute("UPDATE "+table+" SET context_json=NULL,note='' WHERE context_created<?",(cutoff,))


class HumanHandoff:
    def __init__(self, client, store, departments, element_url, *, secure=False):
        self.client, self.store = client, store
        self.secure=secure
        self.departments = {d: (u,) if isinstance(u, str) else tuple(u) for d, u in departments.items()}
        if any(not users for users in self.departments.values()):
            raise ValueError('Department roster is empty')
        for domain, roster in self.departments.items():
            self.store.backfill_legacy(domain, roster[0])
        self.element_url = element_url.rstrip('/')
        self.locks, self.department_locks = {}, {}

    def directory(self, user, domain):
        assigned = self.store.assignee(user, domain)
        prefix = '!юрдеп' if domain == 'legal' else '!финдеп'
        lines = [f'**{LABELS[domain]} — выбор сотрудника**', '']
        for number, employee in enumerate(self.departments[domain], 1):
            suffix = ' — ваша текущая переписка' if employee == assigned else ''
            lines.append(f'{number}. {employee}{suffix}. Команда: `{prefix}{number}`')
        lines.extend(['', '`!другой` — следующий сотрудник, кроме текущего. Можно дописать описание вопроса.',
                      'Список показывает настроенные аккаунты, а не присутствие или время ожидания. '
                      'При смене создаётся отдельная комната; предыдущая сохраняется.'])
        return '\n'.join(lines)

    async def open(self, user, domain, context=None, note='', *, employee_number=None, change=False, request_id=None):
        if domain not in self.departments:
            raise ValueError('Department is not configured')
        self.store.expire_context()
        roster = self.departments[domain]
        if employee_number is not None and not 1 <= employee_number <= len(roster):
            raise HandoffChoiceError(f'Выберите номер от 1 до {len(roster)}. Список: !сотрудники.')
        lock = self.locks.setdefault((user, domain), asyncio.Lock())
        async with lock, self.department_locks.setdefault(domain, asyncio.Lock()):
            saved = self.store.request_record(request_id, user, domain) or self.store.record(user, domain)
            retry = bool(request_id and self.store.request_record(request_id, user, domain))
            assigned = saved['assignee'] if saved else None
            migrating=bool(self.secure and saved and not self.store.encrypted_room(saved['room']))
            if saved and not saved['prepared'] and not retry and not migrating and (
                    change or (employee_number is not None and roster[employee_number-1] != assigned)):
                raise HandoffChoiceError('Предыдущая передача ещё не завершена. Повторите !человек, затем выберите другого сотрудника.')
            target, next_position = assigned, None
            if not retry and (saved is None or saved['prepared'] or migrating):
                if employee_number is not None:
                    target = roster[employee_number - 1]
                elif change and assigned:
                    choices = [u for u in roster if u != assigned]
                    if not choices:
                        raise HandoffChoiceError('В департаменте настроен только один сотрудник. Другой пока недоступен.')
                    start = (roster.index(assigned) + 1) % len(roster) if assigned in roster else 0
                    target = next(roster[(start+i) % len(roster)] for i in range(len(roster)) if roster[(start+i) % len(roster)] != assigned)
                elif saved is None:
                    target, next_position = self.store.next_employee(domain, roster)
                if saved is None or target != assigned or migrating:
                    inherited = json.loads(saved['context_json'] or 'null') if saved else None
                    transferred = (inherited or context) if saved else context
                    transferred_note = note or (saved['note'] or '' if saved else '')
                    response = await self.client.room_create(
                        name=f'{LABELS[domain]} — сотрудник {roster.index(target)+1} — {user}',
                        topic=('Личная зашифрованная переписка с сотрудником. Для передачи контекста оба участника принимают приглашение и подтверждают устройство бота.' if self.secure else 'Переписка с живым сотрудником. ИИ здесь не отвечает. Тестовая комната без E2EE.'),
                        preset=RoomPreset.private_chat, federate=False, invite=[user, target],
                        initial_state=[{'type': 'm.room.history_visibility', 'state_key': '', 'content': {'history_visibility': 'invited'}}, *([{'type':'m.room.encryption','state_key':'','content':{'algorithm':'m.megolm.v1.aes-sha2'}},{'type':'m.room.guest_access','state_key':'','content':{'guest_access':'forbidden'}}] if self.secure else [])],
                        power_level_override={'users': {self.client.user: 100, user: 0 if self.secure else 100, target: 0 if self.secure else 100}, 'invite': 100, 'state_default':100})
                    if not isinstance(response, RoomCreateResponse):
                        raise RuntimeError('Could not create human room')
                    self.store.create(user, domain, response.room_id, target, next_position,
                                      context=transferred, note=transferred_note, request_id=request_id,encrypted=self.secure)
                    saved = self.store.record(user, domain)
            self.store.remember(request_id, user, domain, saved['room'])
            if self.secure:
                await self._prepare_secure(saved)
                return self._link(self.store.room_record(saved['room']))
            if not saved['prepared']:
                transferred = json.loads(saved['context_json'] or 'null')
                response = await self.client.room_send(room_id=saved['room'], message_type='m.room.message',
                    content={'msgtype': 'm.text', 'body': context_message(user, transferred, saved['note'] or '')})
                if not isinstance(response, RoomSendResponse):
                    raise RuntimeError('Could not transfer context')
                self.store.mark_room(saved['room'], 'prepared')
                saved = self.store.room_record(saved['room'])
            return self._link(saved)

    def _link(self, saved):
        url = self.element_url + '/#/room/' + quote(saved['room'], safe='!:')
        label = LABELS[saved['domain']]
        body = (f'Переписка с {label}: {url}\nНазначен сотрудник: {saved["assignee"]}.\n'
                'Примите приглашение и пишите сотруднику напрямую. Его присутствие сейчас не подтверждено.\n')
        body += 'Бот вышел из этой комнаты.' if saved['departed'] else ('Бот передаст первоначальный контекст только после входа обоих участников и проверки их устройств, затем выйдет.' if self.store.encrypted_room(saved['room']) else 'Бот здесь молчит и выйдет, когда первый человек примет приглашение.')
        body += ('\nДля смены вернитесь в этот чат ИИ: !сотрудники — выбрать по номеру, !другой — следующий. '
                 'Предыдущая комната сохраняется. Переписка людей автоматически не передаётся; '
                 'новому сотруднику доступен только переданный вопрос, ответ ИИ и ваш комментарий. '
                 'В существующей комнате новый вопрос напишите самостоятельно.')
        return {'msgtype': 'm.text', 'body': body, 'format': 'org.matrix.custom.html',
                'formatted_body': html.escape(body.split(url)[0]) + f'<a href="{html.escape(url, quote=True)}">Открыть переписку с {label}</a><br>' + html.escape(body.split(url, 1)[1]).replace('\n', '<br>')}

    async def member_joined(self, room, user):
        saved = self.store.room_record(room)
        if saved is None or user not in {saved['user'], saved['assignee']}:
            return
        lock = self.locks.setdefault((saved['user'], saved['domain']), asyncio.Lock())
        async with lock:
            saved = self.store.room_record(room)
            if self.secure:
                await self._prepare_secure(saved)
                return
            if not saved['prepared'] or saved['departed']:
                return
            response = await self.client.room_leave(room)
            if not isinstance(response, RoomLeaveResponse):
                raise RuntimeError('Could not leave human room')
            self.store.mark_room(room, 'departed')


    async def _prepare_secure(self, saved):
        if saved['departed']:return True
        room=self.client.rooms.get(saved['room'])
        expected={self.client.user,saved['user'],saved['assignee']}
        if room is None or not room.encrypted or set(room.users)!=expected:return False
        if {u for u,member in room.users.items() if not getattr(member,'invited',False)}!=expected:return False
        if not saved['prepared']:
            from nio.exceptions import OlmUnverifiedDeviceError
            from matrix_crypto import verified_recipients_only,CryptoPolicyError
            import hashlib
            try:
                verified_recipients_only(self.client,room)
                response=await self.client.room_send(room_id=saved['room'],message_type='m.room.message',
                    content={'msgtype':'m.text','body':context_message(saved['user'],json.loads(saved['context_json'] or 'null'),saved['note'] or '')},
                    tx_id=hashlib.sha256(('sts-handoff-context\0'+saved['room']).encode()).hexdigest(),ignore_unverified_devices=False)
            except (OlmUnverifiedDeviceError,CryptoPolicyError):return False
            if not isinstance(response,RoomSendResponse):return False
            self.store.mark_room(saved['room'],'prepared')
        response=await self.client.room_leave(saved['room'])
        if not isinstance(response,RoomLeaveResponse):return False
        self.store.mark_room(saved['room'],'departed')
        return True

    async def flush_secure(self):
        if not self.secure:return
        rooms=[r[0] for r in self.store.db.execute('SELECT room FROM handoffs WHERE departed=0')]
        for room in rooms:
            saved=self.store.room_record(room)
            if not self.store.encrypted_room(room):continue
            async with self.locks.setdefault((saved['user'],saved['domain']),asyncio.Lock()):
                await self._prepare_secure(self.store.room_record(room))
