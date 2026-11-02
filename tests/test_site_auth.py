"""Вход на сайт ресторана: подпись кнопки Telegram и сессия в cookie."""
import hashlib
import hmac
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import site_auth

TOKEN = '123456:test-only'
NOW = 1_800_000_000


def widget(**fields):
    data = {'id': 42, 'first_name': 'Тест', 'username': 'test', 'auth_date': NOW, **fields}
    check = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
    data['hash'] = hmac.new(hashlib.sha256(TOKEN.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


class LoginTests(unittest.TestCase):
    def test_valid_login_gives_user(self):
        self.assertEqual(site_auth.verify_login(widget(), TOKEN, NOW), 42)

    def test_changed_field_or_other_token_is_rejected(self):
        self.assertIsNone(site_auth.verify_login({**widget(), 'id': 43}, TOKEN, NOW))
        self.assertIsNone(site_auth.verify_login(widget(), '654321:other', NOW))

    def test_mini_app_signature_does_not_work_as_login(self):
        data = {'id': 42, 'auth_date': NOW}
        check = '\n'.join(f'{k}={data[k]}' for k in sorted(data))
        secret = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
        data['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        self.assertIsNone(site_auth.verify_login(data, TOKEN, NOW))

    def test_old_unknown_or_broken_data_is_rejected(self):
        self.assertIsNone(site_auth.verify_login(widget(), TOKEN, NOW + site_auth.LOGIN_MAX_AGE + 1))
        self.assertIsNone(site_auth.verify_login(widget(extra='x'), TOKEN, NOW))
        self.assertIsNone(site_auth.verify_login(widget(id='abc'), TOKEN, NOW))
        for broken in (None, [], {}, {'hash': ''}):
            self.assertIsNone(site_auth.verify_login(broken, TOKEN, NOW))


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


if __name__ == '__main__':
    unittest.main()
