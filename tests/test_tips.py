import os,sys,unittest
from datetime import date
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tips_stats import compare_tips,summarize


def entry(day,amount,category='Чаевые',kind='income'):
    return {'created_at':day,'signed_amount':amount,'category':category,'kind':kind}


class TipsTests(unittest.TestCase):
    def test_gross_expenses_net_no_salary(self):
        e=[entry('2026-09-01T20:00:00+03:00',2000),entry('2026-09-02T01:00:00+03:00',1000),entry('2026-09-02T02:00:00+03:00',-300,kind='expense'),entry('2026-09-02T12:00:00+03:00',30000,'Зарплата')]
        r=summarize(e,date(2026,9,1),date(2026,9,30),date(2026,9,26))
        self.assertEqual((r['gross'],r['expenses'],r['net'],r['shifts'],r['avg_gross'],r['avg_net']),(3000,300,2700,1,3000,2700))
    def test_weighted_average_by_shifts_not_entries(self):
        e=[entry('2026-09-01T12:00:00+03:00',1000),entry('2026-09-01T13:00:00+03:00',1000),entry('2026-09-02T12:00:00+03:00',4000)]
        r=compare_tips(e,today=date(2026,9,26))['a'];self.assertEqual(r['avg_gross'],3000)
    def test_incomplete_months_align(self):
        r=compare_tips([],today=date(2026,9,13));self.assertEqual(r['b']['end'],'2026-08-13');self.assertTrue(r['aligned'])
    def test_full_months_keep_lengths(self):
        r=compare_tips([],anchor=date(2026,8,1),other=date(2026,2,1),today=date(2026,9,26))
        self.assertEqual(r['a']['end'],'2026-08-31');self.assertEqual(r['b']['end'],'2026-02-28')
    def test_overview_stays_full_when_comparator_is_incomplete(self):
        e=[entry('2026-08-03T12:00:00+03:00',1000),entry('2026-08-27T12:00:00+03:00',2000),entry('2026-08-27T13:00:00+03:00',-300,kind='expense')]
        options={'anchor':date(2026,8,1),'today':date(2026,9,13)}
        previous=compare_tips(e,**options)
        partial=compare_tips(e,other=date(2026,9,1),**options)
        whole=compare_tips(e,other=date(2026,9,1),aligned=False,**options)
        self.assertEqual(partial['a']['end'],'2026-08-13')
        self.assertEqual(partial['a']['net'],1000)
        self.assertEqual(partial['overview'],previous['overview'])
        self.assertEqual(partial['overview'],whole['overview'])
        self.assertEqual((partial['overview']['end'],partial['overview']['net'],partial['overview']['shifts']),('2026-08-31',2700,2))
    def test_overview_preserves_negative_and_missing_data(self):
        options={'anchor':date(2026,8,1),'other':date(2026,9,1),'today':date(2026,9,13)}
        r=compare_tips([entry('2026-08-20T12:00:00+03:00',-200,kind='expense')],**options)
        self.assertFalse(r['a']['has_data'])
        self.assertTrue(r['overview']['has_data'])
        self.assertEqual(r['overview']['net'],-200)
        self.assertEqual(r['overview']['shifts'],0)
        self.assertIsNone(r['overview']['avg_gross'])
        self.assertIsNone(r['overview']['avg_net'])
        empty=compare_tips([],**options)['overview']
        self.assertFalse(empty['has_data'])
        self.assertEqual(empty['net'],0)
        self.assertIsNone(empty['avg_gross'])
    def test_leap_year(self):
        r=compare_tips([],anchor=date(2024,2,1),today=date(2026,9,26));self.assertEqual(r['a']['end'],'2024-02-29')
    def test_weeks_cross_year_and_align(self):
        r=compare_tips([],kind='week',today=date(2026,1,1));self.assertEqual(r['a']['start'],'2025-12-29');self.assertEqual(r['b']['end'],'2025-12-25')
    def test_negative_and_missing_not_growth(self):
        e=[entry('2026-08-03T12:00:00+03:00',-100,kind='expense'),entry('2026-09-03T12:00:00+03:00',-200,kind='expense')]
        r=compare_tips(e,today=date(2026,9,26));self.assertEqual(r['a']['net'],-200);self.assertIsNone(r['a']['avg_gross']);self.assertIsNone(r['delta']['net']['pct']);self.assertEqual(r['delta']['net']['amount'],-100)
        r=compare_tips([],today=date(2026,9,26));self.assertFalse(r['a']['has_data']);self.assertIsNone(r['delta']['net']['amount'])
    def test_end_boundary_and_future_excluded(self):
        e=[entry('2026-09-27T12:00:00+03:00',5000),entry('2026-09-01T01:00:00+03:00',1000)]
        r=compare_tips(e,today=date(2026,9,26));self.assertFalse(r['a']['has_data'])
    def test_future_selection_rejected(self):
        with self.assertRaises(ValueError):compare_tips([],anchor=date(2026,10,1),today=date(2026,9,26))

if __name__=='__main__':unittest.main()
