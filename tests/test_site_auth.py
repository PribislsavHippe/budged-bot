"""Вход на сайт ресторана через бота и сессия в cookie."""
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import site_auth

TOKEN = '123456:test-only'
NOW = 1_800_000_000


class LoginFlowTests(unittest.TestCase):
    def setUp(self):
        site_auth.PENDING.clear()
        site_auth.BY_POLL.clear()

    def test_confirmed_login_is_handed_to_the_waiting_browser_once(self):
        poll_id, token, code = site_auth.start_login(NOW)
        self.assertRegex(code, r'^\d{4}$')
        self.assertEqual(site_auth.poll(poll_id, NOW), ('pending',))
        self.assertEqual(site_auth.claim(token, 42, NOW), code)
        self.assertTrue(site_auth.decide(token, 42, True, NOW))
        self.assertEqual(site_auth.poll(poll_id, NOW), ('approved', 42))
        self.assertEqual(site_auth.poll(poll_id, NOW), ('expired',))

    def test_only_the_person_who_opened_the_link_decides(self):
        poll_id, token, _ = site_auth.start_login(NOW)
        site_auth.claim(token, 42, NOW)
        self.assertIsNone(site_auth.claim(token, 43, NOW))
        self.assertFalse(site_auth.decide(token, 43, True, NOW))
        self.assertEqual(site_auth.poll(poll_id, NOW), ('pending',))

    def test_unclaimed_rejected_old_or_unknown_logins_never_approve(self):
        poll_id, token, _ = site_auth.start_login(NOW)
        self.assertFalse(site_auth.decide(token, 42, True, NOW))
        site_auth.claim(token, 42, NOW)
        self.assertTrue(site_auth.decide(token, 42, False, NOW))
        self.assertFalse(site_auth.decide(token, 42, True, NOW))
        self.assertEqual(site_auth.poll(poll_id, NOW), ('rejected',))
        poll_id, token, _ = site_auth.start_login(NOW)
        later = NOW + site_auth.LOGIN_SECONDS + 1
        self.assertIsNone(site_auth.claim(token, 42, later))
        self.assertEqual(site_auth.poll(poll_id, later), ('expired',))
        self.assertEqual(site_auth.poll('unknown', NOW), ('expired',))
        self.assertIsNone(site_auth.claim('unknown', 42, NOW))

    def test_poll_secret_and_link_token_differ(self):
        poll_id, token, _ = site_auth.start_login(NOW)
        self.assertNotEqual(poll_id, token)
        self.assertEqual(site_auth.poll(token, NOW), ('expired',))

    def test_pending_logins_are_capped_and_expired_ones_freed(self):
        with patch.object(site_auth, 'MAX_PENDING', 2):
            self.assertTrue(site_auth.start_login(NOW))
            self.assertTrue(site_auth.start_login(NOW))
            self.assertIsNone(site_auth.start_login(NOW))
            self.assertTrue(site_auth.start_login(NOW + site_auth.LOGIN_SECONDS + 1))


class SessionTests(unittest.TestCase):
    def test_session_round_trip_and_expiry(self):
        value = site_auth.issue(42, TOKEN, NOW)
        self.assertEqual(site_auth.read(value, TOKEN, NOW + 10), 42)
        self.assertIsNone(site_auth.read(value, TOKEN, NOW + site_auth.SESSION_SECONDS + 1))

    def test_tampered_or_foreign_session_is_rejected(self):
        value = site_auth.issue(42, TOKEN, NOW)
        user, expires, signature = value.split('.')
        for forged in (f'43.{expires}.{signature}', f'{user}.{int(expires) + 999}.{signature}', value + 'x', '', None, 'a.b'):
            self.assertIsNone(site_auth.read(forged, TOKEN, NOW), forged)
        self.assertIsNone(site_auth.read(value, '654321:other', NOW))


class BotTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid', 'SUPABASE_KEY': 'test'}), patch('supabase.create_client'):
            import site_login_chat
        cls.chat = site_login_chat

    def setUp(self):
        site_auth.PENDING.clear()
        site_auth.BY_POLL.clear()

    def message(self, uid=42):
        return NS(from_user=NS(id=uid), answer=AsyncMock())

    async def test_manager_sees_the_code_and_confirms(self):
        poll_id, token, code = site_auth.start_login()
        message = self.message()
        with patch.object(self.chat, 'has_cabinet', AsyncMock(return_value=True)):
            await self.chat.start_site_login(message, NS(args='site_' + token))
        text = message.answer.await_args.args[0]
        self.assertIn(f'Код на сайте: {code}', text)
        buttons = message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0]
        self.assertEqual([b.callback_data for b in buttons], [f'site:ok:{token}', f'site:no:{token}'])
        callback = NS(data=f'site:ok:{token}', from_user=NS(id=42), answer=AsyncMock(),
                      message=NS(edit_text=AsyncMock(), answer=AsyncMock()))
        await self.chat.decide_site_login(callback)
        callback.message.edit_text.assert_awaited_once_with('Готово, вернись на сайт.')
        self.assertEqual(site_auth.poll(poll_id), ('approved', 42))

    async def test_person_without_cabinet_cannot_log_in(self):
        poll_id, token, _ = site_auth.start_login()
        message = self.message()
        with patch.object(self.chat, 'has_cabinet', AsyncMock(return_value=False)):
            await self.chat.start_site_login(message, NS(args='site_' + token))
        self.assertIn('администратору ресторана', message.answer.await_args.args[0])
        self.assertFalse(site_auth.decide(token, 42, True))
        self.assertEqual(site_auth.poll(poll_id), ('pending',))

    async def test_stale_button_says_the_link_expired(self):
        callback = NS(data='site:ok:unknown', from_user=NS(id=42), answer=AsyncMock(),
                      message=NS(edit_text=AsyncMock(side_effect=RuntimeError('old')), answer=AsyncMock()))
        await self.chat.decide_site_login(callback)
        self.assertIn('устарела', callback.message.answer.await_args.args[0])

    async def test_other_deep_links_pass_through(self):
        from aiogram.dispatcher.event.bases import UNHANDLED
        self.assertIs(await self.chat.start_site_login(self.message(), NS(args='team_abc')), UNHANDLED)


if __name__ == '__main__':
    unittest.main()
