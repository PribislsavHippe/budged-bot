import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from httpx import RemoteProtocolError
from postgrest.exceptions import APIError


def modules():
    with patch.dict(os.environ, {
        'BOT_TOKEN': '123456:abcdefghijklmnopqrstuvwxyzABCDE',
        'SUPABASE_URL': 'https://example.invalid',
        'SUPABASE_KEY': 'test',
    }), patch('supabase.create_client'):
        import db
        import main as startup
    return startup, db


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_recovers_after_transient_supabase_disconnects(self):
        startup, db = modules()
        execute = AsyncMock(side_effect=[RemoteProtocolError('server disconnected')] * 2 + [None] * 5)
        supabase = Mock()
        with patch.object(db, 'supabase', supabase), patch.object(db, '_execute', execute), \
                patch.object(startup.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            await startup.db_ready()
        self.assertEqual(sleep.await_args_list, [call(1), call(2)])
        self.assertEqual([c.args[0] for c in supabase.table.call_args_list], [
            'telegram_inbox', 'telegram_inbox', 'telegram_inbox', 'entries', 'research_subjects',
            'money_source_receipts', 'telegram_actor_state',
        ])

    async def test_startup_reports_persistent_transport_failure_without_migration_advice(self):
        startup, db = modules()
        execute = AsyncMock(side_effect=RemoteProtocolError('private-request-detail'))
        with patch.object(db, 'supabase', Mock()), patch.object(db, '_execute', execute), \
                patch.object(startup.asyncio, 'sleep', new_callable=AsyncMock) as sleep, \
                self.assertLogs(level='ERROR') as logs:
            with self.assertRaisesRegex(RuntimeError, 'подключиться к Supabase'):
                await startup.db_ready()
        self.assertEqual(execute.await_count, 5)
        self.assertEqual(sleep.await_args_list, [call(1), call(2), call(4), call(8)])
        self.assertIn('code=network', '\n'.join(logs.output))
        self.assertNotIn('private-request-detail', '\n'.join(logs.output))

    async def test_startup_reports_missing_schema_without_retrying(self):
        startup, db = modules()
        execute = AsyncMock(side_effect=APIError({'code': '42703', 'message': 'missing column'}))
        with patch.object(db, 'supabase', Mock()), patch.object(db, '_execute', execute), \
                patch.object(startup.asyncio, 'sleep', new_callable=AsyncMock) as sleep, self.assertLogs(level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'миграции v24–v26'):
                await startup.db_ready()
        execute.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_failed_startup_keeps_jobs_and_webhook_stopped(self):
        startup, _ = modules()
        bot = SimpleNamespace(session=SimpleNamespace(middleware=Mock(), close=AsyncMock()))
        with patch.multiple(
            startup, Bot=Mock(return_value=bot), build_dispatcher=Mock(), WEBHOOK_HOST='https://example.invalid',
            db_ready=AsyncMock(side_effect=RuntimeError('unavailable')), setup_scheduler=Mock(),
            run_webhook=AsyncMock(),
        ):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                await startup.main()
            startup.setup_scheduler.assert_not_called()
            startup.run_webhook.assert_not_awaited()
        bot.session.close.assert_awaited_once()

    async def test_webhook_starts_after_database_check(self):
        startup, _ = modules()
        bot = SimpleNamespace(session=SimpleNamespace(middleware=Mock(), close=AsyncMock()))
        scheduler = Mock(running=True)
        dispatcher = Mock()

        async def ready():
            scheduler.start.assert_not_called()

        async def webhook(bot_arg, dispatcher_arg):
            scheduler.start.assert_called_once()
            self.assertIs(bot_arg, bot)
            self.assertIs(dispatcher_arg, dispatcher)

        with patch.multiple(
            startup, Bot=Mock(return_value=bot), build_dispatcher=Mock(return_value=dispatcher),
            WEBHOOK_HOST='https://example.invalid', db_ready=AsyncMock(side_effect=ready),
            setup_scheduler=Mock(return_value=scheduler), run_webhook=AsyncMock(side_effect=webhook),
        ):
            await startup.main()
            startup.db_ready.assert_awaited_once()
            startup.run_webhook.assert_awaited_once()
        scheduler.shutdown.assert_called_once_with(wait=False)
        bot.session.close.assert_awaited_once()
