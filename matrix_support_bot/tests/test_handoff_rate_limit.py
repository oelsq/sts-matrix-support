"""Bounded room creation retry for an explicit server rate-limit rejection."""
import unittest
from unittest.mock import AsyncMock,patch
from nio import RoomCreateResponse,RoomCreateError
from human_handoff import HumanHandoff

class RoomCreationRateLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow=object.__new__(HumanHandoff)
        self.flow.client=type('Client',(),{})()
        self.flow.client.room_create=AsyncMock()

    async def test_explicit_limit_waits_then_reuses_identical_room_configuration(self):
        expected=RoomCreateResponse('!test:demo.local')
        self.flow.client.room_create.side_effect=[RoomCreateError('limited','M_LIMIT_EXCEEDED',500),expected]
        with patch('human_handoff.asyncio.sleep',new_callable=AsyncMock) as sleep:
            actual=await self.flow._create_room_with_rate_limit_retry(name='test',invite=['@user:demo.local'])
        self.assertIs(actual,expected)
        sleep.assert_awaited_once_with(0.6)
        self.assertEqual(self.flow.client.room_create.call_args_list[0],self.flow.client.room_create.call_args_list[1])

    async def test_permanent_limit_is_bounded_to_five_attempts(self):
        limited=RoomCreateError('limited','M_LIMIT_EXCEEDED',1)
        self.flow.client.room_create.return_value=limited
        with patch('human_handoff.asyncio.sleep',new_callable=AsyncMock) as sleep:
            self.assertIs(await self.flow._create_room_with_rate_limit_retry(name='test'),limited)
        self.assertEqual(self.flow.client.room_create.await_count,5)
        self.assertEqual(sleep.await_count,4)

    async def test_other_errors_are_not_retried(self):
        forbidden=RoomCreateError('forbidden','M_FORBIDDEN')
        self.flow.client.room_create.return_value=forbidden
        with patch('human_handoff.asyncio.sleep',new_callable=AsyncMock) as sleep:
            self.assertIs(await self.flow._create_room_with_rate_limit_retry(name='test'),forbidden)
        self.flow.client.room_create.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_excessive_retry_interval_does_not_block_forever(self):
        limited=RoomCreateError('limited','M_LIMIT_EXCEEDED',200000)
        self.flow.client.room_create.return_value=limited
        with patch('human_handoff.asyncio.sleep',new_callable=AsyncMock) as sleep:
            self.assertIs(await self.flow._create_room_with_rate_limit_retry(name='test'),limited)
        self.flow.client.room_create.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_network_failure_is_not_retried_to_avoid_duplicate_rooms(self):
        self.flow.client.room_create.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.flow._create_room_with_rate_limit_retry(name='test')
        self.flow.client.room_create.assert_awaited_once()
