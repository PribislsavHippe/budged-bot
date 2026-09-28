import unittest
from datetime import date
from chat_dates import parse_date
from sales_chat import parse_sales_message,parse_report_edit

class HumanInputTests(unittest.TestCase):
    def test_date_spellings(self):
        today=date(2026,9,28)
        for text in ['2026-09-13','13.09','13/09/26','13-09-2026','13 сентября','по 13 сентября включительно','за 13 сент.','13','13-го']:
            with self.subTest(text=text):self.assertEqual(parse_date(text,today),date(2026,9,13))
        self.assertEqual(parse_date('вчера',date(2026,1,1)),date(2025,12,31))
        self.assertEqual(parse_date('позавчера',today),date(2026,9,26))
    def test_ambiguous_and_invalid_dates_not_guessed(self):
        for text in ['31 сентября','30.09','09/30','2025-02-29','на прошлой неделе','13.09 или 14.09','завтра']:
            with self.subTest(text=text),self.assertRaises(ValueError):parse_date(text,date(2026,9,28))
    def test_flexible_report_and_values(self):
        for text in ['отчёт по 13 сентября, вино:73 238, коктейлей 57 и открыток пять',
                     'запиши отчет 13.09 вино 73 238\nкоктейли=57\nоткрытки:5']:
            result=parse_sales_message(text,date(2026,9,28))
            self.assertEqual(result['cutoff'],'2026-09-13')
            self.assertEqual(result['totals'],{'wine':73238,'cocktails':57,'postcards':5})
        self.assertEqual(parse_sales_message('план на октябрь, вино 143 тыс; оборот 1,57 млн',date(2026,9,28))['targets'],{'wine':143000,'turnover':1570000})
        for text in ['продал 2 коктейля','два коктейля','коктейль два']:
            self.assertEqual(parse_sales_message(text,date(2026,9,28))['value'],2)
    def test_partial_correction_preserves_other_fields(self):
        original={'wine':100,'cocktails':50,'dvd':2}
        cutoff,totals=parse_report_edit('вино: 2,5 тыс',date(2026,9,28),'2026-09-13',original)
        self.assertEqual(cutoff,'2026-09-13');self.assertEqual(totals,{'wine':2500,'cocktails':50,'dvd':2})
        self.assertEqual(original['wine'],100)
        changed,values=parse_report_edit('вчера',date(2026,9,28),cutoff,original)
        self.assertEqual(changed,'2026-09-27');self.assertEqual(values,original)
    def test_no_silent_extra_numbers_or_duplicate_fields(self):
        for text in ['отчет вчера вино 100 20','отчет вчера вино 100 коктейли 1.5',
                     'отчет вчера вино 100 вину 200','отчет вчера вино 100 чаевые 500',
                     'продал 1.5 коктейля','продал что-то 500']:
            with self.subTest(text=text),self.assertRaises(ValueError):parse_sales_message(text,date(2026,9,28))
