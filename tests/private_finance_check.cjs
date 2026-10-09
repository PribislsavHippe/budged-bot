const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const rows=[
  {id:1,kind:'income',account:'cash',signed_amount:4350.16,category:'Чаевые',created_at:'2026-10-02T02:00:00Z',tip_percent:16},
  {id:2,kind:'expense',account:'card',signed_amount:-850,category:'Такси',work_date:'2026-10-01',note:'трата смены'},
  {id:3,kind:'income',account:'card',signed_amount:700,category:'Чаевые',work_date:'2026-09-30',tip_percent:10},
  {id:4,kind:'income',account:'card',signed_amount:50000,category:'Зарплата',work_date:'2026-10-03'},
  {id:5,kind:'accrual',account:'pending',signed_amount:2000,category:'Сервисный сбор',work_date:'2026-10-01'},
  {id:6,kind:'income',account:'card',signed_amount:500,category:'Чаевые',work_date:'2026-10-09',tip_percent:5},
  {id:7,kind:'expense',account:'cash',signed_amount:-100,category:'Мойка',work_date:'2026-10-09'},
];
const today='2026-10-09',shifts=['2026-10-09','2026-11-01','2024-09-01'];
const window={privateMoney:{active:true,entries:rows,state:{today}}};
vm.runInNewContext(fs.readFileSync('webapp/private-finance.js','utf8'),{window,Date,Map,Math,Number,String,Array});
const api=window.PrivateFinance,clone=x=>JSON.parse(JSON.stringify(x));
const reference=JSON.parse(execFileSync(process.env.BUDGET_TEST_PYTHON||'.venv/bin/python',['-c',`
import json,sys
from datetime import date
from stats import compute_stats,compute_month,month_bounds
from tips_stats import compare_tips,summarize
from service_charge import summarize as service
r=json.load(sys.stdin);rows=r['rows'];today=date.fromisoformat(r['today']);shifts=r['shifts']
print(json.dumps({'stats':compute_stats(rows,today,4000),'month':compute_month(rows,shifts,2026,10,today),
 'bounds':month_bounds(rows,shifts,2026,10,today),
 'compare':compare_tips(rows,'month',today,aligned=True,today=today),
 'range':summarize(rows,date(2026,10,1),today,today),'service':service(rows,'2026-10')}))
`],{input:JSON.stringify({rows,today,shifts}),encoding:'utf8'}));
const stats=clone(api.stats(rows,{shift_goal:4000,calendar_shift_dates:shifts},today));
for(const [key,value] of Object.entries(reference.stats))assert.deepEqual(stats[key],value,key);
assert.deepEqual(clone(api.month(rows,shifts,2026,10,today)),reference.month);
for(const [key,value] of Object.entries(reference.bounds))assert.equal(stats[key],value,key);
assert.deepEqual(clone(api.comparison(rows,{kind:'month',aligned:true},today)),reference.compare);
assert.deepEqual(clone(api.summarize(rows,'2026-10-01',today,today)),reference.range);
assert.deepEqual(clone(api.view('/api/service_charge/view',{}, {month:'2026-10',private:true})),{month:'2026-10',private:true,...reference.service});
assert.equal(api.entryDate(rows[0]),'2026-10-01');
const home=clone(api.range(rows,{}, {has_shift:true},today));
assert.equal(home.period.gross,500);assert.equal(home.period.net,400);
window.privateMoney.entries=[];
assert.equal(api.view('/api/tips_range',{}, {has_shift:true,today}).period.gross,0);
console.log('Private finance matches server calculations: money, dates, calendar, comparison, service charge and empty shift: OK');
