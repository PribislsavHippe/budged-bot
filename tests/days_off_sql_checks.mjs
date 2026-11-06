import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const R='aaaaaaaa-0000-0000-0000-000000000001', L='cccccccc-0000-0000-0000-000000000001';
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:27},(_,i)=>'migration_v'+(i+7)+'.sql')]){
    await db.exec(await source(name));
  }
  await db.exec("INSERT INTO users(id) VALUES(1),(2)");
  await db.exec(`INSERT INTO restaurants(id,name,owner_id) VALUES('${R}','Ресторан',1)`);
  await db.exec(`INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES('${L}','${R}',2,'Аня','аня','approved')`);
  const add=(days,start='2026-11-16',end='2026-11-30')=>db.query("INSERT INTO day_off_requests(link_id,restaurant_id,start_date,end_date,days) VALUES($1,$2,$3,$4,$5)",[L,R,start,end,JSON.stringify(days)]);
  await add(['2026-11-17','2026-11-18']);
  const first=(await db.query('SELECT answered,waiting FROM day_off_requests')).rows[0];
  assert.deepEqual(first,{answered:true,waiting:false});
  await db.query("INSERT INTO day_off_requests(link_id,restaurant_id,start_date,end_date,days,answered,waiting) VALUES($1,$2,'2026-12-01','2026-12-15','[]',false,true)",[L,R]);
  assert.equal((await db.query('SELECT count(*) count FROM day_off_requests WHERE waiting')).rows[0].count,1);
  await db.query("DELETE FROM day_off_requests WHERE start_date='2026-12-01'");
  await assert.rejects(add([]),/duplicate|unique/);
  await assert.rejects(add([],'2026-12-10','2026-12-01'),/check/);
  await assert.rejects(add([],'2027-01-01','2027-03-15'),/check/);
  await assert.rejects(add({a:1},'2026-12-01','2026-12-15'),/check/);
  await assert.rejects(add(Array.from({length:13},(_,i)=>'2026-12-'+String(i+1).padStart(2,'0')),'2026-12-01','2026-12-15'),/check/);
  for(const role of ['anon','authenticated']){
    await db.exec('SET ROLE '+role);
    await assert.rejects(db.query('SELECT * FROM day_off_requests'),/permission/);
    await db.exec('RESET ROLE');
  }
  await db.exec(`DELETE FROM employee_links WHERE id='${L}'`);
  assert.equal((await db.query('SELECT count(*) count FROM day_off_requests')).rows[0].count,0);
  console.log('v32–v33 SQL passed: waiting and answered marks, one answer per person and period, period and size limits, no public access, cascade.');
} finally { await db.close(); }
