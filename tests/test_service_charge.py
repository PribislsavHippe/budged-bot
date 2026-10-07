import unittest

from service_charge import parse, summarize
from stats import _net, _tips


class ServiceChargeTests(unittest.TestCase):
    def test_explicit_message_only(self):
        self.assertEqual(parse("сервисный сбор 1 500,50 ₽"), 1500.5)
        self.assertEqual(parse("сс 2000"), 2000)
        self.assertEqual(parse("СС: 2 000 ₽"), 2000)
        self.assertIsNone(parse("1500"))
        self.assertIsNone(parse("кофе 1500"))
        self.assertIsNone(parse("касса 2000"))
        for message in ("сервисный сбор", "сервисный сбор 0", "сервисный сбор 700 кофе",
                        "сс", "сс 0", "сс 2000 кофе"):
            with self.subTest(message=message), self.assertRaises(ValueError):
                parse(message)

    def test_accrual_does_not_raise_received_money(self):
        entries = [
            {"kind": "income", "category": "Чаевые", "signed_amount": 500,
             "work_date": "2026-10-07"},
            {"kind": "accrual", "category": "Сервисный сбор", "signed_amount": 700,
             "work_date": "2026-10-07"},
        ]
        self.assertEqual(_net(entries), 500)
        self.assertEqual(_tips(entries), 500)
        self.assertEqual(summarize(entries, "2026-10"), {"total": 700, "count": 1})
        self.assertEqual(summarize(entries, "2026-09"), {"total": 0, "count": 0})


if __name__ == "__main__":
    unittest.main()
