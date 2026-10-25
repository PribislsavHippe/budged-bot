"""Обрыв соединения с базой не должен задерживать сообщение на минуты."""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
    import db
    from telegram_inbox import TelegramInbox


class Query:
    def __init__(self, method, outcomes):
        self.http_method = method
        self.outcomes = list(outcomes)
        self.calls = 0

    def execute(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


STALE = httpx.RemoteProtocolError('Server disconnected')


class ExecuteTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_survives_a_dropped_connection(self):
        query = Query('GET', [STALE, 'rows'])
        with patch.object(db.asyncio, 'sleep', new=AsyncMock()):
            self.assertEqual(await db._execute(query), 'rows')
        self.assertEqual(query.calls, 2)

    async def test_write_is_not_repeated_unless_the_caller_says_it_is_safe(self):
        query = Query('POST', [STALE, 'done'])
        with self.assertRaises(httpx.RemoteProtocolError):
            await db._execute(query)
        self.assertEqual(query.calls, 1)
        query = Query('POST', [STALE, STALE, 'done'])
        with patch.object(db.asyncio, 'sleep', new=AsyncMock()):
            self.assertEqual(await db._execute(query, retries=2), 'done')
        self.assertEqual(query.calls, 3)

    async def test_timeouts_and_other_errors_are_not_retried(self):
        query = Query('GET', [httpx.ReadTimeout('slow'), 'rows'])
        with self.assertRaises(httpx.ReadTimeout):
            await db._execute(query)
        self.assertEqual(query.calls, 1)

    async def test_gives_up_after_the_retries_and_marks_the_provider(self):
        query = Query('GET', [STALE, STALE])
        with patch.object(db.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaises(httpx.RemoteProtocolError) as caught:
                await db._execute(query)
        self.assertEqual(caught.exception._diagnostic_provider, 'supabase')
        self.assertEqual(query.calls, 2)


class InboxTests(unittest.IsolatedAsyncioTestCase):
    def inbox(self):
        bot = NS(token='123:abc')
        return TelegramInbox(bot, NS())

    async def test_lost_claim_answer_releases_the_lease_at_once(self):
        inbox = self.inbox()
        calls = []

        def rpc(name, params):
            calls.append((name, params))
            return NS(name=name)

        async def execute(query, retries=None):
            if query.name == 'claim_telegram_batch':
                raise STALE
            return NS(data=None)

        with patch.object(db, 'supabase', NS(rpc=rpc)), patch.object(db, '_execute', new=execute):
            with self.assertRaises(httpx.RemoteProtocolError):
                await inbox.process([{'update_id': 7, 'batch_id': None}])
        names = [name for name, _ in calls]
        self.assertEqual(names, ['claim_telegram_batch', 'finish_telegram_batch'])
        claim, finish = calls[0][1], calls[1][1]
        self.assertEqual((finish['batch'], finish['token'], finish['succeeded']), (claim['batch'], claim['token'], False))

    async def test_a_new_message_wakes_one_idle_worker_not_all(self):
        inbox = self.inbox()
        loop = asyncio.get_running_loop()
        waiters = [loop.create_future() for _ in range(5)]
        inbox.idle.extend(waiters)
        inbox.notify()
        self.assertEqual([w.done() for w in waiters], [True, False, False, False, False])

    async def test_message_arriving_while_everyone_is_busy_is_not_lost(self):
        inbox = self.inbox()
        inbox.pending = False
        inbox.notify()
        self.assertTrue(inbox.pending)


if __name__ == '__main__':
    unittest.main()
