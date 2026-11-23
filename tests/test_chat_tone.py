import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from chat_tone import friend_text


class FriendTextTests(unittest.TestCase):
    def test_variant_requires_matching_positive_private_chat(self):
        with patch.dict(os.environ, {'FRIEND_TELEGRAM_ID': '42'}):
            self.assertEqual(friend_text(42, 'Обычно.', 'Приятель!'), 'Приятель!')
            private = SimpleNamespace(chat=SimpleNamespace(id=42, type='private'))
            self.assertEqual(friend_text(private, 'Обычно.', 'Приятель!'), 'Приятель!')
            group = SimpleNamespace(chat=SimpleNamespace(id=42, type='group'))
            for recipient in (group, -42, 7, None):
                with self.subTest(recipient=recipient):
                    self.assertEqual(friend_text(recipient, 'Обычно.', 'Приятель!'), 'Обычно.')

    def test_empty_or_invalid_setting_keeps_standard_text(self):
        for configured in ('', 'Женя', '-42', '@friend'):
            with self.subTest(configured=configured), patch.dict(os.environ, {'FRIEND_TELEGRAM_ID': configured}):
                self.assertEqual(friend_text(42, 'Обычно.', 'Приятель!'), 'Обычно.')
