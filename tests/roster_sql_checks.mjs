import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:24},(_,i)=>'migration_v'+(i+7)+'.sql')]){
    await db.exec(await source(name));
  }
  await db.exec("INSERT INTO users(id) VALUES(1),(2)");
  await db.exec("INSERT INTO restaurants(id,name,owner_id) VALUES('aaaaaaaa-0000-0000-0000-000000000001','Ресторан',1)");
  await db.exec("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES('cccccccc-0000-0000-0000-000000000001','aaaaaaaa-0000-0000-0000-000000000001',2,'Аня','аня','approved')");
  assert.equal((await db.query('SELECT can_schedule FROM employee_links')).rows[0].can_schedule,false);
  const add=(id,start,end)=>db.query("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft) VALUES($1,'aaaaaaaa-0000-0000-0000-000000000001',$2,$3,'{}')",[id,start,end]);
  await add('dddddddd-0000-0000-0000-000000000001','2026-11-16','2026-11-30');
  assert.equal((await db.query('SELECT revision,published FROM rosters')).rows[0].revision,1);
  await assert.rejects(add('dddddddd-0000-0000-0000-000000000002','2026-11-16','2026-11-20'),/duplicate|unique/);
  await assert.rejects(add('dddddddd-0000-0000-0000-000000000003','2026-12-10','2026-12-01'),/check/);
  await assert.rejects(add('dddddddd-0000-0000-0000-000000000004','2027-01-01','2027-03-15'),/check/);
  await assert.rejects(db.query("UPDATE rosters SET published='[]'"),/check/);
  for(const role of ['anon','authenticated']){
    await db.exec('SET ROLE '+role);
    await assert.rejects(db.query('SELECT * FROM rosters'),/permission/);
    await db.exec('RESET ROLE');
  }
  await db.exec("DELETE FROM restaurants");
  assert.equal((await db.query('SELECT count(*) count FROM rosters')).rows[0].count,0);
  console.log('v30 SQL passed: schedule rights default off, one schedule per start date, period limits, no public access, cascade.');
} finally { await db.close(); }
