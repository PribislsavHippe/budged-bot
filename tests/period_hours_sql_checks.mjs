// v45: часы из виджета выходных заменяют правила «только с … до …» на оставшиеся дни периода.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const R='aaaaaaaa-0000-0000-0000-000000000001';
const A='cccccccc-0000-0000-0000-000000000001';
const P='dddddddd-0000-0000-0000-000000000001';
const op=n=>'eeeeeeee-0000-0000-0000-'+String(n).padStart(12,'0');
const write=async(n,hours,{actor=2,period=P,today='2026-11-18'}={})=>(await db.query(
  'SELECT write_period_hours($1,$2,$3,$4,$5) AS result',[actor,op(n),period,JSON.stringify(hours),today])).rows[0].result;
const rules=async()=>(await db.query("SELECT target,kind,start_min,end_min FROM shift_time_rules ORDER BY target,kind")).rows;
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:39},(_,i)=>'migration_v'+(i+7)+'.sql')]) await db.exec(await source(name));
  await db.exec('INSERT INTO users(id) VALUES(1),(2),(3)');
  await db.query("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Тест',1)",[R]);
  await db.query("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES($1,$2,2,'Аля','аля','approved')",[A,R]);
  await db.query("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft) VALUES($1,$2,'2026-11-16','2026-11-30',$3)",[P,R,JSON.stringify({people:[{link:A}]})]);
  // Прежние правила: прошедший день, будущий день, правило по дню недели и пожелание на дату — последние два не трогаем.
  await db.query(`INSERT INTO shift_time_rules(link_id,restaurant_id,target,kind,start_min,end_min,operation_id) VALUES
    ($1,$2,'2026-11-17','limit',600,900,$3),($1,$2,'2026-11-20','limit',600,900,$3),($1,$2,'4','limit',720,NULL,$3),
    ($1,$2,'2026-11-21','wish',540,NULL,$3)`,[A,R,op(99)]);
  await db.exec('SET ROLE service_role');
  assert.equal(await write(1,{},{actor:3}),'access');
  assert.equal(await write(2,{'2026-11-17':[540,1020]}),'period');            // прошедший день
  assert.equal(await write(3,{'2026-12-01':[540,1020]}),'period');
  for(const [n,bad] of [[4,{'2026-11-22':[540,540]}],[5,{'2026-11-22':[300,1020]}],[6,{'2026-11-22':[540,1400]}],
                        [7,{'2026-11-22':[540]}],[8,{'2026-11-22':['9',1020]}],[9,{'2026-11-22':[540.5,1020]}],
                        [10,[]],[11,{'22.11':[540,1020]}],[12,{'2026-02-30':[540,1020]}]])
    assert.equal(await write(n,bad),'invalid',JSON.stringify(bad));
  assert.equal((await rules()).length,4,'refusals change nothing');
  assert.equal(await write(13,{'2026-11-22':[540,1020],'2026-11-23':[600,1380]}),'ok');
  assert.equal(await write(13,{'2026-11-22':[540,1020],'2026-11-23':[600,1380]}),'ok');   // повтор
  assert.deepEqual(await rules(),[
    {target:'2026-11-17',kind:'limit',start_min:600,end_min:900},
    {target:'2026-11-21',kind:'wish',start_min:540,end_min:null},
    {target:'2026-11-22',kind:'limit',start_min:540,end_min:1020},
    {target:'2026-11-23',kind:'limit',start_min:600,end_min:1380},
    {target:'4',kind:'limit',start_min:720,end_min:null}]);
  assert.equal(await write(14,{}),'ok');                                        // часов нет — будущие даты очищены
  assert.deepEqual((await rules()).map(r=>r.target),['2026-11-17','2026-11-21','4']);
  await db.exec('RESET ROLE; SET ROLE anon');
  await assert.rejects(write(15,{}),/permission/);
  console.log('v45 period hours: access, period bounds, strict values, replay, replacement of future date limits only.');
} finally {await db.close();}
