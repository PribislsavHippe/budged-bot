import unittest
from calendar_feed import render_shifts


class CalendarFeedTests(unittest.TestCase):
    def test_timed_and_all_day_shifts_use_moscow_and_crlf(self):
        rows = [
            {'shift_date':'2026-10-05','starts_at':'14:00:00','ends_at':'23:30:00',
             'calendar_updated_at':'2026-10-04T15:00:00+03:00'},
            {'shift_date':'2026-10-06','starts_at':None,'ends_at':None,
             'calendar_updated_at':'2026-10-04T15:00:00+03:00'},
        ]
        data = render_shifts(rows, 42, 'test-token')
        self.assertIn(b'DTSTART:20261005T110000Z\r\n',data)
        self.assertIn(b'DTEND:20261005T203000Z\r\n',data)
        self.assertIn(b'DTSTART;VALUE=DATE:20261006\r\n',data)
        self.assertIn(b'DTEND;VALUE=DATE:20261007\r\n',data)
        self.assertEqual(data.count(b'BEGIN:VEVENT'),2)
        self.assertTrue(data.endswith(b'END:VCALENDAR\r\n'))
        self.assertFalse(data.replace(b'\r\n',b'').count(b'\n'))

    def test_overnight_shift_and_stable_ids(self):
        row = {'shift_date':'2026-10-05','starts_at':'22:00','ends_at':'02:00',
               'calendar_updated_at':'2026-10-04T12:00:00Z'}
        original = render_shifts([row],42,'test-token')
        modified = render_shifts([{**row,'ends_at':'03:00'}],42,'test-token')
        self.assertIn(b'DTEND:20261005T230000Z',original)
        uid = next(line for line in original.split(b'\r\n') if line.startswith(b'UID:'))
        self.assertIn(uid,modified)

    def test_no_money_or_personal_identity_and_valid_line_lengths(self):
        row = {'shift_date':'2026-10-05','starts_at':'10:00','ends_at':'22:00',
               'calendar_updated_at':'2026-10-04T12:00:00Z',
               'signed_amount':5000,'category':'Чаевые','name':'Private Name'}
        data = render_shifts([row],42,'test-token')
        self.assertNotIn(b'5000',data)
        self.assertNotIn(b'Private Name',data)
        self.assertNotIn(b'UID:42@',data)
        self.assertTrue(all(len(line)<=75 for line in data.split(b'\r\n')))


if __name__ == '__main__':
    unittest.main()
