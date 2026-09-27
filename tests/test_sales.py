import os
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sales import compute_sales, number, validate_values, METRICS


def event(day, kind, value, **kw):
    return dict(work_date=f'2026-09-{day:02}', kind=kind, value=value, **kw)


def report(day, totals, version=1):
    return dict(id=str(version), cutoff=f'2026-09-{day:02}', totals=totals,
                created_at=f'2026-09-26T00:00:{version:02}+00:00')


class SalesTests(unittest.TestCase):
    def test_official_example(self):
        targets = dict(wine=143000, cocktails=110, desserts=82000, turnover=1570000)
        totals = dict(wine=73238, cocktails=57, desserts=33724, turnover=919783.5, postcards=5, dvd=2)
        result = compute_sales('2026-09', {'targets': targets, 'glass_price':850}, [], [report(13, totals)])
        self.assertEqual(result['metrics']['wine']['remaining'], 69762)
        self.assertEqual(result['metrics']['turnover']['remaining'], 650216.5)
        self.assertEqual(result['metrics']['cocktails']['pct'], 51.82)
        self.assertEqual(result['metrics']['postcards']['total'], 5)
        self.assertIsNone(result['metrics']['postcards']['target'])

    def test_inclusive_cutoff_and_next_shift(self):
        events = [event(13,'glass',100),event(14,'glass',2),event(14,'bottle',3000)]
        row = compute_sales('2026-09',None,events,[report(13,{'wine':73238})])['metrics']['wine']
        self.assertEqual(row['total'],77938)
        self.assertEqual(row['estimated'],1700)
        self.assertEqual(row['recorded'],3000)

    def test_reports_do_not_add_cumulative_totals(self):
        r = [report(13,{'wine':73238}),report(20,{'wine':100000},2)]
        self.assertEqual(compute_sales('2026-09',None,[event(14,'glass',2),event(21,'glass',1)],r)['metrics']['wine']['total'],100850)

    def test_report_correction_preserves_other_cutoffs(self):
        r=[report(13,{'wine':70000,'cocktails':57}),report(13,{'wine':73238,'cocktails':57},2)]
        result=compute_sales('2026-09',None,[event(14,'glass',1)],r)
        self.assertEqual(result['metrics']['wine']['total'],74088)
        self.assertEqual(len(result['reports']),1)
        self.assertEqual(len(r),2)

    def test_partial_report_uses_independent_cutoffs(self):
        r=[report(13,{'wine':73238,'cocktails':57}),report(20,{'cocktails':80},2)]
        events=[event(14,'glass',1),event(14,'cocktails',20),event(21,'cocktails',1)]
        rows=compute_sales('2026-09',None,events,r)['metrics']
        self.assertEqual(rows['wine']['total'],74088)
        self.assertEqual(rows['cocktails']['total'],81)

    def test_missing_is_not_zero(self):
        rows=compute_sales('2026-09',None,[],[report(13,{'wine':0})])['metrics']
        self.assertEqual(rows['wine']['total'],0)
        self.assertIsNone(rows['cocktails']['total'])

    def test_undo_and_price_change(self):
        events=[event(14,'glass',2),event(14,'bottle',5000,voided=True)]
        r=[report(13,{'wine':73238})]
        row=compute_sales('2026-09',{'glass_price':900},events,r)['metrics']['wine']
        self.assertEqual(row['total'],75038)
        self.assertEqual(row['official'],73238)

    def test_next_month_does_not_inherit(self):
        row=compute_sales('2026-10',None,[event(14,'glass',1)],[report(13,{'wine':73238})])['metrics']['wine']
        self.assertIsNone(row['total'])

    def test_pre_reconciliation_forecast_excludes_future(self):
        row=compute_sales('2026-09',None,[event(12,'glass',2),event(14,'glass',100)],[],through='2026-09-13')['metrics']['wine']
        self.assertEqual(row['total'],1700)

    def test_turnover_not_automatically_added(self):
        rows=compute_sales('2026-09',None,[event(14,'bottle',3000),event(14,'desserts',500)],[])['metrics']
        self.assertIsNone(rows['turnover']['total'])

    def test_money_exactness_and_input_validation(self):
        self.assertEqual(number('250,50'),250.5)
        for value in [True, None, 'NaN','Infinity',-1,0,'0.001',100000001]:
            with self.subTest(value=value),self.assertRaises(ValueError):number(value)
        with self.assertRaises(ValueError):number(1.5,count=True)
        with self.assertRaises(ValueError):validate_values({'salary':100},METRICS)
        rows=compute_sales('2026-09',None,[event(14,'bottle',0.1),event(14,'bottle',0.2)],[])['metrics']
        self.assertEqual(rows['wine']['total'],0.3)


class ChatInputTests(unittest.TestCase):
    def parse(self,text):
        from sales_chat import parse_sales_message
        from datetime import date
        return parse_sales_message(text,date(2026,9,26))
    def test_fast_count(self):
        self.assertEqual(self.parse('бокал')['value'],1)
        self.assertEqual(self.parse('коктейль 2')['kind'],'cocktails')
        self.assertEqual(self.parse('бутылка 1250,50')['value'],1250.5)
    def test_earnings_untouched(self):
        for text in ['чай 500','кофе 200','смена 2500','работаю 26 27']:
            self.assertIsNone(self.parse(text))
    def test_malformed_sales_never_fall_through(self):
        for text in ['бокал 1.5','бутылка','вино 500','коктейль завтра 2','отчет завтра вино 100']:
            with self.subTest(text=text),self.assertRaises(ValueError):self.parse(text)
    def test_month_plan_and_report(self):
        self.assertEqual(self.parse('план продаж 2026-10 вино 143000; коктейли 110')['month'],'2026-10')
        r=self.parse('отчёт 2026-09-13 вино 73238; открытки 5; двд 2')
        self.assertEqual(r['totals'],{'wine':73238,'postcards':5,'dvd':2})
        self.assertEqual(self.parse('цена бокала 900')['glass_price'],900)

if __name__=='__main__':unittest.main()
