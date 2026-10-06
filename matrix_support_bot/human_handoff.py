"""Explicit handoff to human Matrix accounts. No LLM is used here."""
import asyncio
import html
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from nio import RoomCreateResponse, RoomLeaveResponse, RoomPreset, RoomSendResponse

LABELS = {"legal": "ЮрДеп", "finance": "ФинДеп"}


def department_roster(environ):
    """Multiple employees per department, with legacy single-account fallback."""
    teams = {}
    for domain, prefix in [('legal', 'LEGAL'), ('finance', 'FINANCE')]:
        raw = environ.get(prefix + '_DEPARTMENT_USERS') or environ.get(prefix + '_DEPARTMENT_USER', '')
        users = tuple(x.strip() for x in raw.split(',') if x.strip())
        if any(not re.fullmatch(r'@[^\s:,]+:[^\s,]+', x) for x in users):
            raise ValueError('Invalid department Matrix user')
        if len(set(users)) != len(users):
            raise ValueError('Duplicate employee in department roster')
        if users: teams[domain] = users
    return teams


def handoff_command(text):
    """Only explicit commands authorize copying this user's context."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    command = parts[0].casefold()
    commands = {"!человек": "auto", "!юрдеп": "legal", "!финдеп": "finance"}
    if command not in commands:
        return None
    return commands[command], parts[1] if len(parts) > 1 else ""


def choose_department(requested, fixed_domain, previous_domains):
    if requested in LABELS:
        return requested
    if fixed_domain in LABELS:
        return fixed_domain
    if len(previous_domains) == 1 and previous_domains[0] in LABELS:
        return previous_domains[0]
    return None


def context_message(user, context, note):
    question, answer = context or ("", "")
    lines = [
        "Обращение к живому сотруднику от " + user + ".",
        "Пользователь запросил передачу командой. Ответ ИИ ниже может быть ошибочным.",
    ]
    if question:
        lines.extend(["\nПоследний вопрос пользователя:", question[:12000]])
    if answer:
        lines.extend(["\nПредыдущий ответ ИИ (для проверки сотрудником):", answer[:14000]])
    if note:
        lines.extend(["\nКомментарий пользователя:", note[:12000]])
    if not question and not note:
        lines.append("Пожалуйста, опишите свой вопрос в этой комнате.")
    lines.append("\nДальше здесь общаются люди. Бот выходит и не отвечает в этой комнате.")
    return "\n".join(lines)


class HandoffStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS handoffs (
            user TEXT NOT NULL, domain TEXT NOT NULL, room TEXT NOT NULL UNIQUE,
            prepared INTEGER NOT NULL DEFAULT 0, departed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user, domain))""")
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(handoffs)')}
        if 'assignee' not in columns:
            self.db.execute('ALTER TABLE handoffs ADD COLUMN assignee TEXT')
        self.db.execute('CREATE TABLE IF NOT EXISTS department_rotation (domain TEXT PRIMARY KEY, next_position INTEGER NOT NULL)')
        self.db.commit()

    def assignee(self, user, domain):
        row = self.db.execute('SELECT assignee FROM handoffs WHERE user=? AND domain=?', (user, domain)).fetchone()
        return row[0] if row else None

    def next_employee(self, domain, roster):
        row = self.db.execute('SELECT next_position FROM department_rotation WHERE domain=?', (domain,)).fetchone()
        position = (row[0] if row else 0) % len(roster)
        return roster[position], (position + 1) % len(roster)

    def backfill_legacy(self, domain, employee):
        self.db.execute('UPDATE handoffs SET assignee=? WHERE domain=? AND assignee IS NULL', (employee, domain))
        self.db.commit()

    def get(self, user, domain):
        return self.db.execute(
            "SELECT room, prepared, departed FROM handoffs WHERE user=? AND domain=?",
            (user, domain),
        ).fetchone()

    def create(self, user, domain, room, assignee=None, next_position=None):
        with self.db:
            self.db.execute("INSERT INTO handoffs(user,domain,room,assignee) VALUES(?,?,?,?)", (user, domain, room, assignee))
            if next_position is not None:
                self.db.execute('INSERT INTO department_rotation(domain,next_position) VALUES(?,?) ON CONFLICT(domain) DO UPDATE SET next_position=excluded.next_position', (domain, next_position))

    def mark(self, user, domain, column):
        if column not in {"prepared", "departed"}:
            raise ValueError("Invalid status")
        self.db.execute(f"UPDATE handoffs SET {column}=1 WHERE user=? AND domain=?", (user, domain))
        self.db.commit()

    def contains_room(self, room):
        return self.db.execute("SELECT 1 FROM handoffs WHERE room=?", (room,)).fetchone() is not None

    def for_room(self, room):
        return self.db.execute("SELECT user,domain,prepared,departed FROM handoffs WHERE room=?", (room,)).fetchone()


class HumanHandoff:
    def __init__(self, client, store, departments, element_url):
        self.client, self.store = client, store
        self.departments = {domain: (users,) if isinstance(users, str) else tuple(users) for domain, users in departments.items()}
        if any(not users for users in self.departments.values()):
            raise ValueError('Department roster is empty')
        for domain, roster in self.departments.items():
            self.store.backfill_legacy(domain, roster[0])
        self.element_url = element_url.rstrip("/")
        self.locks = {}
        self.department_locks = {}

    async def open(self, user, domain, context=None, note=""):
        if domain not in self.departments:
            raise ValueError("Department is not configured")
        lock = self.locks.setdefault((user, domain), asyncio.Lock())
        async with lock, self.department_locks.setdefault(domain, asyncio.Lock()):
            saved = self.store.get(user, domain)
            if saved is None:
                department, next_position = self.store.next_employee(domain, self.departments[domain])
                response = await self.client.room_create(
                    name=f"{LABELS[domain]} — обращение {user}",
                    topic="Переписка с живым сотрудником. ИИ здесь не отвечает. Тестовая комната без E2EE.",
                    preset=RoomPreset.private_chat,
                    federate=False,
                    invite=[user, department],
                    initial_state=[{
                        "type": "m.room.history_visibility", "state_key": "",
                        "content": {"history_visibility": "invited"},
                    }],
                    power_level_override={
                        "users": {self.client.user: 100, user: 100, department: 100},
                        "invite": 100,
                    },
                )
                if not isinstance(response, RoomCreateResponse):
                    raise RuntimeError("Could not create human room")
                self.store.create(user, domain, response.room_id, department, next_position)
                saved = self.store.get(user, domain)
            room, prepared, departed = saved
            if not prepared:
                response = await self.client.room_send(
                    room_id=room, message_type="m.room.message",
                    content={"msgtype": "m.text", "body": context_message(user, context, note)},
                )
                if not isinstance(response, RoomSendResponse):
                    raise RuntimeError("Could not transfer context")
                self.store.mark(user, domain, "prepared")
            url = self.element_url + "/#/room/" + quote(room, safe="!:")
            label = LABELS[domain]
            assigned = self.store.assignee(user, domain)
            body = (
                f"Переписка с {label}: {url}\n"
                f"Назначен сотрудник: {assigned}.\n"
                "Примите приглашение в эту комнату и пишите сотруднику напрямую. "
                "Сотруднику отправлено приглашение. Ответ появится, когда он войдёт и ответит; "
                "его присутствие сейчас не подтверждено. "
            )
            body += "Бот вышел из этой комнаты." if departed else "Бот здесь молчит и выйдет, когда первый человек примет приглашение."
            if prepared and departed:
                body += "\nЭто существующая переписка; новый вопрос напишите там самостоятельно."
            return {
                "msgtype": "m.text", "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html.escape(body.split(url)[0])
                + f'<a href="{html.escape(url, quote=True)}">Открыть переписку с {label}</a><br>'
                + html.escape(body.split(url, 1)[1]).replace("\n", "<br>"),
            }

    async def member_joined(self, room, user):
        """Keep new room alive until a human joins, then cease membership."""
        saved = self.store.for_room(room)
        if saved is None:
            return
        requester, domain, prepared, departed = saved
        if user not in {requester, self.store.assignee(requester, domain)}:
            return
        lock = self.locks.setdefault((requester, domain), asyncio.Lock())
        async with lock:
            saved = self.store.for_room(room)
            if not saved[2] or saved[3]:
                return
            response = await self.client.room_leave(room)
            if not isinstance(response, RoomLeaveResponse):
                raise RuntimeError("Could not leave human room")
            self.store.mark(requester, domain, "departed")
