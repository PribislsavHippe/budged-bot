import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const R='aaaaaaaa-0000-0000-0000-000000000001';
const A='cccccccc-0000-0000-0000-000000000001';
const P='dddddddd-0000-0000-0000-000000000001';
const op=n=>'eeeeeeee-0000-0000-0000-'+String(n).padStart(12,'0');
const write=async(n,{actor=2, remove=null, period=P, targets=['2026-11-23','2026-11-25'], kind='limit',
                       start=540,end=1020}={})=>(await db.query('SELECT write_shift_time_rules($1,$2,$3,$4,$5,$6,$7,$8,$9) AS result',
  [actor,op(n),remove,period,targets,kind,start,end,'2026-11-12'])).rows[0].result;
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:32},(_,i)=>'migration_v'+(i+7)+'.sql')]) await db.exec(await source(name));
  await db.exec('INSERT INTO users(id) VALUES(1),(2),(3)');
  await db.query("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Тест',1)",[R]);
  await db.query("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES($1,$2,2,'Аля','аля','approved')",[A,R]);
  await db.query("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft) VALUES($1,$2,'2026-11-16','2026-11-30',$3)",[P,R,JSON.stringify({people:[{link:A}]})]);
  await db.query("INSERT INTO day_off_requests(link_id,restaurant_id,roster_id,start_date,end_date,days) VALUES($1,$2,$3,'2026-11-16','2026-11-30',$4)",
    [A,R,P,JSON.stringify(['2026-11-20'])]);
  await db.exec('SET ROLE service_role');
  assert.equal(await write(1,{actor:3}),'access');
  assert.equal(await write(2,{targets:['2026-11-22','2026-12-05']}),'period');
  assert.equal((await db.query('SELECT * FROM shift_time_rules')).rows.length,0);
  assert.equal(await write(3),'ok');
  assert.equal(await write(3),'ok');
  assert.deepEqual((await db.query('SELECT target,start_min,end_min FROM shift_time_rules ORDER BY target')).rows,
    [{target:'2026-11-23',start_min:540,end_min:1020},{target:'2026-11-25',start_min:540,end_min:1020}]);
  assert.deepEqual((await db.query('SELECT days,answered FROM day_off_requests')).rows,
    [{days:['2026-11-20'],answered:true}]);
  assert.equal(await write(4,{targets:['2026-11-23'],start:570}),'ok');
  assert.equal(await write(5,{remove:op(3),period:null}),'ok');
  assert.equal(await write(5,{remove:op(3),period:null}),'ok');
  assert.equal((await db.query('SELECT target FROM shift_time_rules')).rows[0].target,'2026-11-23');
  assert.equal(await write(6,{period:null, targets:['6'],kind:'wish',start:840,end:null}),'ok');
  await db.exec('RESET ROLE; SET ROLE anon');
  await assert.rejects(db.query('SELECT * FROM shift_time_rules'),/permission/);
  await assert.rejects(write(7),/permission/);
  console.log('v38 shift time rules: access, exact dates, atomic replacement, replay, deletion and weekly rules passed.');
} finally {await db.close();}
