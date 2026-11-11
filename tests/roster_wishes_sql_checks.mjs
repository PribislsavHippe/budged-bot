import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const R='aaaaaaaa-0000-0000-0000-000000000001';
const A='cccccccc-0000-0000-0000-000000000001';
const B='cccccccc-0000-0000-0000-000000000002';
const P='dddddddd-0000-0000-0000-000000000001';
const Q='dddddddd-0000-0000-0000-000000000002';
const oldStart='2026-11-16', oldEnd='2026-11-30';
const draft=JSON.stringify({people:[{name:'Аня',link:A},{name:'Боря',link:B}]});
const exec=(sql,...args)=>db.query(sql,args);
const write=async({actor=2,link=A,period=P,operation,revision=null,changes={},weekdays=null,pattern=null,clear=false}={})=>{
  const row=await exec('SELECT write_roster_wishes($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) AS result',
    actor,link,period,operation,revision,JSON.stringify(changes),weekdays===null?null:JSON.stringify(weekdays),pattern,clear,'2026-11-11');
  return row.rows[0].result;
};
const op=n=>'eeeeeeee-0000-0000-0000-'+String(n).padStart(12,'0');
const claim=async(time,id=null)=>(await exec('SELECT claim_roster_wish_notice($1,$2) AS claim',time,id)).rows[0].claim;
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:30},(_,i)=>'migration_v'+(i+7)+'.sql')]){
    await db.exec(await source(name));
  }
  await db.exec('INSERT INTO users(id) VALUES(1),(2),(3),(4)');
  await exec("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Тест',1)",R);
  await exec("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES($1,$3,2,'Аня','аня','approved'),($2,$3,3,'Боря','боря','approved')",A,B,R);
  await exec('INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft) VALUES($1,$2,$3,$4,$5)',P,R,oldStart,oldEnd,draft);
  await exec('INSERT INTO day_off_requests(link_id,restaurant_id,start_date,end_date,days) VALUES($1,$2,$3,$4,$5)',A,R,oldStart,oldEnd,'["2026-11-18"]');
  await db.exec(await source('migration_v37.sql'));
  assert.equal((await exec('SELECT collect_wishes_at FROM rosters WHERE id=$1',P)).rows[0].collect_wishes_at,null);
  assert.deepEqual((await exec('SELECT roster_id,desired_days,days FROM day_off_requests WHERE link_id=$1',A)).rows[0],
    {roster_id:P,desired_days:[],days:['2026-11-18']});

  await db.exec('SET ROLE service_role');
  assert.equal((await write({actor:4,operation:op(1),changes:{'2026-11-18':'preferred'}})).status,'access');
  assert.equal((await write({operation:op(2),changes:{'2026-12-18':'preferred'},pattern:'3/3'})).status,'period');
  assert.deepEqual((await exec('SELECT * FROM weekday_wishes')).rows,[]);
  const mixed=await write({operation:op(3),revision:1,
    changes:{'2026-11-18':'preferred','2026-11-22':'required'},weekdays:[1],pattern:'3/3'});
  assert.deepEqual(mixed,{status:'ok',revision:2,days:['2026-11-22'],desired_days:['2026-11-18'],weekdays:[1],pattern:'3/3'});
  assert.equal((await write({operation:op(4),revision:1,changes:{'2026-11-18':'none'}})).status,'conflict');
  const edit=await write({operation:op(5),revision:2,changes:{'2026-11-18':'none'}});
  assert.deepEqual(edit.desired_days,[]);
  assert.deepEqual(await write({operation:op(3),revision:1,changes:{'2026-11-18':'preferred'}}),mixed);
  assert.deepEqual((await exec('SELECT desired_days FROM day_off_requests WHERE link_id=$1',A)).rows[0].desired_days,[]);

  // A trigger failure rolls back the period and all linked replies together.
  await db.exec('RESET ROLE');
  await db.exec("ALTER TABLE day_off_requests ADD CHECK (start_date <> '2026-11-15')");
  await assert.rejects(exec("UPDATE rosters SET start_date='2026-11-15' WHERE id=$1",P),/check/);
  assert.equal((await exec('SELECT start_date::text AS start FROM rosters WHERE id=$1',P)).rows[0].start,oldStart);
  assert.deepEqual((await exec('SELECT days FROM day_off_requests WHERE roster_id=$1',P)).rows[0].days,['2026-11-22']);
  await exec("UPDATE rosters SET start_date='2026-11-17',end_date='2026-11-20' WHERE id=$1",P);
  const moved=(await exec('SELECT days,desired_days,revision,roster_id FROM day_off_requests WHERE link_id=$1',A)).rows[0];
  assert.deepEqual(moved,{days:[],desired_days:[],revision:4,roster_id:P});
  await exec('UPDATE day_off_requests SET waiting=true WHERE roster_id=$1',P);
  await exec('DELETE FROM rosters WHERE id=$1',P);
  assert.deepEqual((await exec('SELECT roster_id,waiting FROM day_off_requests WHERE link_id=$1',A)).rows[0],{roster_id:null,waiting:false});
  await exec("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft,collect_wishes_at) VALUES($1,$2,'2026-11-17','2026-11-20',$3,NULL)",P,R,draft);
  assert.equal((await exec('SELECT roster_id FROM day_off_requests WHERE link_id=$1',A)).rows[0].roster_id,null);

  await exec("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft,collect_wishes_at) VALUES($1,$2,'2026-11-22','2026-11-30',$3,'2026-11-11T04:00Z')",Q,R,draft);
  assert.equal(await claim('2026-11-11T06:59:59Z'),null);
  await db.exec('SET ROLE service_role');
  const first=await claim('2026-11-11T07:00:00Z');
  assert.equal(first.link_id,A);
  assert.equal(first.stage,1);
  await exec("UPDATE roster_wish_notices SET sent_at='2026-11-11T07:00:00Z' WHERE roster_id=$1 AND link_id=$2",Q,A);
  assert.equal((await claim('2026-11-11T07:00:00Z',Q)).link_id,B);
  await exec("UPDATE roster_wish_notices SET sent_at='2026-11-11T07:00:00Z' WHERE roster_id=$1 AND link_id=$2",Q,B);
  assert.equal((await write({actor:3,link:B,period:Q,operation:op(6),clear:true})).status,'ok');
  assert.equal(await claim('2026-11-13T06:59:59Z'),null);
  assert.equal(await claim('2026-11-13T07:00:00Z',Q),null);
  const repeat=await claim('2026-11-13T07:00:00Z');
  assert.equal(repeat.stage,2);
  assert.equal(repeat.link_id,A);
  assert.equal(await claim('2026-11-13T07:00:00Z'),null);
  const manual=await claim('2026-11-13T08:00:00Z',P);
  assert.equal(manual.stage,1);
  await exec("UPDATE roster_wish_notices SET sent_at='2026-11-13T08:00:00Z' WHERE roster_id=$1 AND link_id=$2",P,manual.link_id);
  const manualRepeat=await claim('2026-11-15T08:00:00Z');
  assert.equal(manualRepeat.stage,2);
  assert.equal(manualRepeat.link_id,manual.link_id);
  assert.equal(await claim('2026-11-15T08:00:00Z'),null);
  await db.exec('RESET ROLE');
  const other='aaaaaaaa-0000-0000-0000-000000000002';
  await exec("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Другой ресторан',4)",other);
  await exec('UPDATE employee_links SET restaurant_id=$1 WHERE id=$2',other,A);
  await db.exec('SET ROLE service_role');
  assert.equal((await write({operation:op(7),changes:{'2026-11-18':'preferred'}})).status,'access');
  const transferred=await write({period:null,operation:op(8),pattern:'2/2'});
  assert.deepEqual(transferred.weekdays,[]);
  assert.deepEqual((await exec('SELECT restaurant_id,days,pattern FROM weekday_wishes WHERE link_id=$1',A)).rows[0],
    {restaurant_id:other,days:[],pattern:'2/2'});
  await db.exec('RESET ROLE');
  await db.exec('SET ROLE anon');
  await assert.rejects(db.query('SELECT * FROM roster_wish_notices'),/permission/);
  await assert.rejects(claim('2026-11-13T07:00:00Z'),/permission/);
  console.log('v37 wishes passed: migration, access, atomic edit, replay, date moves, and reminder deduplication.');
} finally {await db.close();}
