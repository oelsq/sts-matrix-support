import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from nio import RoomCreateResponse, RoomLeaveResponse, RoomSendResponse
from human_handoff import HandoffStore, HumanHandoff, choose_department, handoff_command


class CommandsTest(unittest.TestCase):
    def test_only_explicit_commands_copy_context(self):
        self.assertIsNone(handoff_command("Мне ответ не помог"))
        self.assertIsNone(handoff_command("Расскажи про !человек"))
        self.assertEqual(handoff_command("!человек Нужен расчёт"), ("auto", "Нужен расчёт"))

    def test_ambiguous_route_requires_department(self):
        self.assertIsNone(choose_department("auto", None, ("legal", "finance")))
        self.assertEqual(choose_department("auto", "legal", ("finance",)), "legal")
        self.assertEqual(choose_department("finance", "legal", ()), "finance")


class HandoffTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = HandoffStore(self.path)
        self.client = AsyncMock()
        self.client.user = "@bot:demo.local"
        self.client.room_create.side_effect = [RoomCreateResponse("!one:demo.local"), RoomCreateResponse("!two:demo.local")]
        self.client.room_send.return_value = RoomSendResponse("$event", "!one:demo.local")
        self.client.room_leave.return_value = RoomLeaveResponse()
        self.handoff = HumanHandoff(self.client, self.store,
            {"legal": "@jurdep:demo.local", "finance": "@findep:demo.local"}, "http://localhost:18081")

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    async def test_private_room_has_only_requester_and_correct_department_invites(self):
        result = await self.handoff.open("@alice:demo.local", "legal", ("Мой вопрос", "Ответ ИИ"), "Комментарий")
        created = self.client.room_create.call_args.kwargs
        self.assertEqual(created["invite"], ["@alice:demo.local", "@jurdep:demo.local"])
        self.assertFalse(created["federate"])
        self.assertEqual(created["power_level_override"]["users"]["@jurdep:demo.local"], 100)
        self.assertIn("Мой вопрос", self.client.room_send.call_args.kwargs["content"]["body"])
        self.client.room_leave.assert_not_awaited()
        await self.handoff.member_joined("!one:demo.local", "@alice:demo.local")
        self.client.room_leave.assert_awaited_once_with("!one:demo.local")
        self.assertIn("!one:demo.local", result["body"])

    async def test_repeated_request_after_restart_reuses_room_without_bot_rejoining(self):
        await self.handoff.open("@alice:demo.local", "legal")
        await self.handoff.member_joined("!one:demo.local", "@alice:demo.local")
        reopened = HandoffStore(self.path)
        try:
            handoff = HumanHandoff(self.client, reopened, self.handoff.departments, "http://localhost:18081")
            await handoff.open("@alice:demo.local", "legal", ("Новый вопрос", "Новый ответ"))
        finally:
            reopened.db.close()
        self.assertEqual(self.client.room_create.await_count, 1)
        self.assertEqual(self.client.room_send.await_count, 1)
        self.assertEqual(self.client.room_leave.await_count, 1)

    async def test_two_users_never_share_human_room(self):
        await self.handoff.open("@alice:demo.local", "finance")
        await self.handoff.open("@bob:demo.local", "finance")
        self.assertNotEqual(self.store.get("@alice:demo.local", "finance")[0], self.store.get("@bob:demo.local", "finance")[0])
        self.assertEqual(self.client.room_create.call_args.kwargs["invite"], ["@bob:demo.local", "@findep:demo.local"])

    async def test_failure_leaving_does_not_claim_completion_and_retry_recovers(self):
        self.client.room_leave.return_value = object()
        await self.handoff.open("@alice:demo.local", "legal")
        with self.assertRaises(RuntimeError):
            await self.handoff.member_joined("!one:demo.local", "@alice:demo.local")
        self.assertEqual(self.store.get("@alice:demo.local", "legal"), ("!one:demo.local", 1, 0))
        self.client.room_leave.return_value = RoomLeaveResponse()
        await self.handoff.member_joined("!one:demo.local", "@alice:demo.local")
        self.assertEqual(self.client.room_create.await_count, 1)
        self.assertEqual(self.client.room_send.await_count, 1)
        self.assertEqual(self.store.get("@alice:demo.local", "legal")[2], 1)

    async def test_concurrent_requests_create_one_room(self):
        await asyncio.gather(*[self.handoff.open("@alice:demo.local", "legal") for _ in range(2)])
        self.assertEqual(self.client.room_create.await_count, 1)

    async def test_department_join_allows_departure_but_outsider_does_not(self):
        await self.handoff.open("@alice:demo.local", "legal")
        await self.handoff.member_joined("!one:demo.local", "@bob:demo.local")
        self.client.room_leave.assert_not_awaited()
        await self.handoff.member_joined("!one:demo.local", "@jurdep:demo.local")
        self.client.room_leave.assert_awaited_once()

    async def test_no_link_when_creation_fails(self):
        self.client.room_create.side_effect = None
        self.client.room_create.return_value = object()
        with self.assertRaises(RuntimeError):
            await self.handoff.open("@alice:demo.local", "legal")
        self.assertIsNone(self.store.get("@alice:demo.local", "legal"))

class TeamsTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        HandoffTest.tearDown(self)

    def setUp(self):
        HandoffTest.setUp(self)
        self.legal = tuple('@jurdep' + (str(i) if i > 1 else '') + ':demo.local' for i in range(1, 6))
        self.finance = tuple('@findep' + (str(i) if i > 1 else '') + ':demo.local' for i in range(1, 6))
        self.team = HumanHandoff(self.client, self.store, {'legal': self.legal, 'finance': self.finance}, 'http://localhost:18081')
        self.client.room_create.side_effect = [RoomCreateResponse(f'!room{i}:demo.local') for i in range(30)]

    async def test_six_requests_rotate_and_domains_have_separate_cursors(self):
        for i in range(6):
            await self.team.open(f'@u{i}:demo.local', 'legal')
            self.assertEqual(self.client.room_create.call_args.kwargs['invite'], [f'@u{i}:demo.local', self.legal[i % 5]])
        await self.team.open('@u0:demo.local', 'finance')
        self.assertEqual(self.store.assignee('@u0:demo.local', 'finance'), self.finance[0])

    async def test_concurrent_users_each_get_one_of_five_employees(self):
        await asyncio.gather(*(self.team.open(f'@u{i}:demo.local', 'finance') for i in range(5)))
        assigned = {self.store.assignee(f'@u{i}:demo.local', 'finance') for i in range(5)}
        self.assertEqual(assigned, set(self.finance))
        self.assertEqual(self.client.room_create.await_count, 5)

    async def test_restart_keeps_employee_and_rotation(self):
        await self.team.open('@alice:demo.local', 'legal')
        other = HandoffStore(self.path)
        try:
            team = HumanHandoff(self.client, other, self.team.departments, 'http://localhost:18081')
            await team.open('@alice:demo.local', 'legal')
            await team.open('@bob:demo.local', 'legal')
            self.assertEqual(other.assignee('@alice:demo.local', 'legal'), self.legal[0])
            self.assertEqual(other.assignee('@bob:demo.local', 'legal'), self.legal[1])
            self.assertEqual(self.client.room_create.await_count, 2)
        finally:
            other.db.close()

    async def test_creation_failure_does_not_skip_an_employee(self):
        self.client.room_create.side_effect = [object(), RoomCreateResponse('!ok:demo.local')]
        with self.assertRaises(RuntimeError):
            await self.team.open('@alice:demo.local', 'legal')
        await self.team.open('@bob:demo.local', 'legal')
        self.assertEqual(self.store.assignee('@bob:demo.local', 'legal'), self.legal[0])

    async def test_unassigned_employee_cannot_trigger_departure(self):
        await self.team.open('@alice:demo.local', 'legal')
        await self.team.member_joined('!room0:demo.local', self.legal[1])
        self.client.room_leave.assert_not_awaited()
        await self.team.member_joined('!room0:demo.local', self.legal[0])
        self.client.room_leave.assert_awaited_once()

    async def test_context_retry_keeps_assignment_and_one_room(self):
        self.client.room_send.return_value = object()
        with self.assertRaises(RuntimeError):
            await self.team.open('@alice:demo.local', 'legal')
        self.client.room_send.return_value = RoomSendResponse('$ok', '!room0:demo.local')
        await self.team.open('@alice:demo.local', 'legal')
        self.assertEqual(self.client.room_create.await_count, 1)
        self.assertEqual(self.store.assignee('@alice:demo.local', 'legal'), self.legal[0])
        await self.team.open('@bob:demo.local', 'legal')
        self.assertEqual(self.store.assignee('@bob:demo.local', 'legal'), self.legal[1])

class RosterTest(unittest.TestCase):
    def test_multiple_employees_and_legacy_fallback(self):
        from human_handoff import department_roster
        result = department_roster({'LEGAL_DEPARTMENT_USERS': '@one:demo.local, @two:demo.local', 'FINANCE_DEPARTMENT_USER': '@old:demo.local'})
        self.assertEqual(result, {'legal': ('@one:demo.local', '@two:demo.local'), 'finance': ('@old:demo.local',)})

    def test_invalid_and_duplicate_employee_rejected(self):
        from human_handoff import department_roster
        for value in ['not-a-matrix-user', '@one:demo.local,@one:demo.local']:
            with self.assertRaises(ValueError):
                department_roster({'LEGAL_DEPARTMENT_USERS': value})

    def test_existing_database_migrates_without_replacing_human_room(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.sqlite3'
            old = sqlite3.connect(path)
            old.execute('CREATE TABLE handoffs (user TEXT NOT NULL, domain TEXT NOT NULL, room TEXT NOT NULL UNIQUE, prepared INTEGER NOT NULL DEFAULT 0, departed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user,domain))')
            old.execute("INSERT INTO handoffs VALUES('@alice:demo.local','legal','!old:demo.local',1,1)")
            old.commit(); old.close()
            store = HandoffStore(path)
            try:
                client = AsyncMock(); client.user = '@bot:demo.local'
                HumanHandoff(client, store, {'legal': ('@jurdep:demo.local', '@jurdep2:demo.local')}, 'http://localhost:18081')
                self.assertEqual(store.get('@alice:demo.local', 'legal'), ('!old:demo.local', 1, 1))
                self.assertEqual(store.assignee('@alice:demo.local', 'legal'), '@jurdep:demo.local')
            finally:
                store.db.close()

class EmployeeChangesTest(unittest.IsolatedAsyncioTestCase):
    setUp = TeamsTest.setUp
    tearDown = TeamsTest.tearDown

    async def test_choose_second_employee_then_switch_to_third_and_keep_old_room(self):
        first = await self.team.open('@alice:demo.local', 'legal', ('Вопрос', 'Ответ'), employee_number=2, request_id='$a')
        self.assertEqual(self.store.assignee('@alice:demo.local', 'legal'), self.legal[1])
        await self.team.member_joined('!room0:demo.local', '@alice:demo.local')
        second = await self.team.open('@alice:demo.local', 'legal', change=True, request_id='$b')
        self.assertEqual(self.store.assignee('@alice:demo.local', 'legal'), self.legal[2])
        self.assertTrue(self.store.contains_room('!room0:demo.local'))
        self.assertEqual(self.store.room_record('!room0:demo.local')['assignee'], self.legal[1])
        self.assertIn('Вопрос', self.client.room_send.call_args.kwargs['content']['body'])
        self.assertNotEqual(first['body'], second['body'])
        self.assertEqual(self.client.room_create.call_args.kwargs['invite'], ['@alice:demo.local', self.legal[2]])
        self.client.join.assert_not_awaited()

    async def test_same_selection_is_reused_but_other_domain_is_independent(self):
        await self.team.open('@alice:demo.local', 'legal', employee_number=2)
        await self.team.open('@alice:demo.local', 'legal', employee_number=2)
        await self.team.open('@alice:demo.local', 'finance', employee_number=5)
        self.assertEqual(self.client.room_create.await_count, 2)
        self.assertEqual(self.store.assignee('@alice:demo.local', 'legal'), self.legal[1])
        self.assertEqual(self.store.assignee('@alice:demo.local', 'finance'), self.finance[4])

    async def test_duplicate_switch_event_after_restart_does_not_switch_twice(self):
        await self.team.open('@alice:demo.local', 'finance', employee_number=2)
        await self.team.open('@alice:demo.local', 'finance', change=True, request_id='$switch')
        other = HandoffStore(self.path)
        try:
            team = HumanHandoff(self.client, other, self.team.departments, 'http://localhost:18081')
            await team.open('@alice:demo.local', 'finance', change=True, request_id='$switch')
            self.assertEqual(other.assignee('@alice:demo.local', 'finance'), self.finance[2])
            self.assertEqual(self.client.room_create.await_count, 2)
        finally:
            other.db.close()

    async def test_replayed_archived_event_never_replaces_current_assignment(self):
        await self.team.open('@alice:demo.local', 'legal', request_id='$first')
        await self.team.open('@alice:demo.local', 'legal', change=True, request_id='$second')
        result = await self.team.open('@alice:demo.local', 'legal', request_id='$first')
        self.assertIn('!room0:demo.local', result['body'])
        self.assertEqual(self.store.assignee('@alice:demo.local', 'legal'), self.legal[1])
        self.assertEqual(self.client.room_create.await_count, 2)

    async def test_late_old_room_join_marks_old_room_only(self):
        await self.team.open('@alice:demo.local', 'legal')
        await self.team.open('@alice:demo.local', 'legal', change=True)
        await self.team.member_joined('!room0:demo.local', self.legal[0])
        self.assertEqual(self.store.room_record('!room0:demo.local')['departed'], 1)
        self.assertEqual(self.store.get('@alice:demo.local', 'legal')[2], 0)
        await self.team.member_joined('!room1:demo.local', self.legal[0])
        self.assertEqual(self.client.room_leave.await_count, 1)

    async def test_failed_switch_preserves_previous_assignment(self):
        await self.team.open('@alice:demo.local', 'finance', employee_number=2)
        self.client.room_create.side_effect = [object()]
        with self.assertRaises(RuntimeError):
            await self.team.open('@alice:demo.local', 'finance', change=True)
        self.assertEqual(self.store.assignee('@alice:demo.local', 'finance'), self.finance[1])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM handoff_archive').fetchone()[0], 0)

    async def test_context_send_retry_keeps_selected_employee_and_original_context(self):
        await self.team.open('@alice:demo.local', 'legal', ('Исходный вопрос', 'Исходный ответ'))
        self.client.room_send.return_value = object()
        with self.assertRaises(RuntimeError):
            await self.team.open('@alice:demo.local', 'legal', employee_number=4, note='Проверьте расчёт', request_id='$transfer')
        self.client.room_send.return_value = RoomSendResponse('$ok', '!room1:demo.local')
        await self.team.open('@alice:demo.local', 'legal', employee_number=4, request_id='$transfer')
        self.assertEqual(self.client.room_create.await_count, 2)
        body = self.client.room_send.call_args.kwargs['content']['body']
        self.assertIn('Исходный вопрос', body)
        self.assertIn('Проверьте расчёт', body)
        self.assertEqual(self.store.assignee('@alice:demo.local', 'legal'), self.legal[3])

    async def test_invalid_selection_and_single_employee_never_create_extra_room(self):
        from human_handoff import HandoffChoiceError
        for number in (0, 6, -1):
            with self.assertRaises(HandoffChoiceError):
                await self.team.open('@alice:demo.local', 'legal', employee_number=number)
        self.client.room_create.assert_not_awaited()
        await self.handoff.open('@alice:demo.local', 'legal')
        with self.assertRaises(HandoffChoiceError):
            await self.handoff.open('@alice:demo.local', 'legal', change=True)
        self.assertEqual(self.client.room_create.await_count, 1)

    async def test_user_cannot_replay_someone_elses_event_or_receive_context(self):
        await self.team.open('@alice:demo.local', 'legal', ('PRIVATE-ALICE', 'Answer'), request_id='$alice')
        with self.assertRaises(ValueError):
            await self.team.open('@bob:demo.local', 'legal', request_id='$alice')
        await self.team.open('@bob:demo.local', 'legal', employee_number=4)
        self.assertNotIn('PRIVATE-ALICE', self.client.room_send.call_args.kwargs['content']['body'])

    async def test_directory_does_not_create_room_or_claim_online_status(self):
        await self.team.open('@alice:demo.local', 'finance', employee_number=2)
        text = self.team.directory('@alice:demo.local', 'finance')
        self.assertIn('!финдеп5', text)
        self.assertIn('ваша текущая переписка', text)
        self.assertIn('не присутствие', text)
        self.assertEqual(self.client.room_create.await_count, 1)

    async def test_duplicate_concurrent_switch_event_creates_one_new_room(self):
        await self.team.open('@alice:demo.local', 'legal')
        await asyncio.gather(*(self.team.open('@alice:demo.local', 'legal', change=True, request_id='$same') for _ in range(3)))
        self.assertEqual(self.client.room_create.await_count, 2)
        self.assertEqual(self.store.assignee('@alice:demo.local', 'legal'), self.legal[1])

    async def test_five_employees_share_500_initial_requests_per_department(self):
        from collections import Counter
        self.client.room_create.side_effect = (RoomCreateResponse(f'!volume{i}:demo.local') for i in range(1000))
        for domain in ('legal','finance'):
            await asyncio.gather(*(self.team.open(f'@worker{i}:demo.local',domain,request_id=f'${domain}{i}') for i in range(500)))
            rows=self.store.db.execute('SELECT assignee,COUNT(*) FROM handoffs WHERE domain=? GROUP BY assignee',(domain,)).fetchall()
            self.assertEqual(Counter(dict(rows)),Counter({employee:100 for employee in self.team.departments[domain]}))
        self.assertEqual(self.store.db.execute('SELECT COUNT(DISTINCT room) FROM handoffs').fetchone()[0],1000)
        self.assertEqual(self.client.room_create.await_count,1000)


def test_numbered_commands_and_change_are_explicit():
    from human_handoff import handoff_selection
    assert handoff_command('!юрдеп3 Хочу другую оценку') == ('legal', 'Хочу другую оценку')
    assert handoff_command('!финдеп5') == ('finance', '')
    assert handoff_selection('!финдеп5') == (5, False)
    assert handoff_selection('!другой Пожалуйста') == (None, True)
    assert handoff_command('Дайте !финдеп5') is None
    assert handoff_command('!финдеп5x') is None
