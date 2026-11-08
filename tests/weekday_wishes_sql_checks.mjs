import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const R='aaaaaaaa-0000-0000-0000-000000000001', L='cccccccc-0000-0000-0000-000000000001';
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:29},(_,i)=>'migration_v'+(i+7)+'.sql')]){
    await db.exec(await source(name));
  }
  await db.exec("INSERT INTO users(id) VALUES(1),(2)");
  await db.exec(`INSERT INTO restaurants(id,name,owner_id) VALUES('${R}','Ресторан',1)`);
  await db.exec(`INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES('${L}','${R}',2,'Аня','аня','approved')`);
  const put=days=>db.query("INSERT INTO weekday_wishes(link_id,restaurant_id,days) VALUES($1,$2,$3) ON CONFLICT(link_id) DO UPDATE SET days=excluded.days",[L,R,JSON.stringify(days)]);
  await put([1]);await put([1,6]);
  assert.deepEqual((await db.query('SELECT days FROM weekday_wishes')).rows[0].days,[1,6]);
  await assert.rejects(put([]),/check/);
  await assert.rejects(put({a:1}),/check/);
  await assert.rejects(put([0,1,2,3,4,5,6,0]),/check/);
  for(const role of ['anon','authenticated']){
    await db.exec('SET ROLE '+role);
    await assert.rejects(db.query('SELECT * FROM weekday_wishes'),/permission/);
    await db.exec('RESET ROLE');
  }
  await db.exec(`DELETE FROM employee_links WHERE id='${L}'`);
  assert.equal((await db.query('SELECT count(*) count FROM weekday_wishes')).rows[0].count,0);
  console.log('v35 SQL passed: one weekday wish per employee, size limits, no public access, cascade.');
} finally { await db.close(); }
