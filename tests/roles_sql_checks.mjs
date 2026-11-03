import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const R='aaaaaaaa-0000-0000-0000-000000000001';
const link=n=>'cccccccc-0000-0000-0000-00000000000'+n;
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:25},(_,i)=>'migration_v'+(i+7)+'.sql')]){
    await db.exec(await source(name));
  }
  await db.exec('INSERT INTO users(id) VALUES(1),(2),(3),(4),(5),(6)');
  await db.exec(`INSERT INTO restaurants(id,name,owner_id) VALUES('${R}','Ресторан',1)`);
  const add=(n,uid,status,extra='')=>db.exec(`INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status${extra?',role':''})
    VALUES('${link(n)}','${R}',${uid},'Имя ${n}','имя ${n}','${status}'${extra?`,'${extra}'`:''})`);
  await add(2,2,'approved','director');await add(3,3,'approved','manager');await add(4,4,'approved');await add(5,5,'pending');await add(6,6,'approved');
  const role=async uid=>(await db.query('SELECT restaurant_role($1,$2) r',[uid,R])).rows[0].r;
  assert.deepEqual([await role(1),await role(2),await role(3),await role(4)],['owner','director','manager',null]);
  const act=(actor,action,n)=>db.query('SELECT identity_action($1,$2,$3)',[actor,action,JSON.stringify({id:link(n),restaurant_id:R})]);
  const roleOf=async n=>(await db.query('SELECT role,status FROM employee_links WHERE id=$1',[link(n)])).rows[0];
  // Менеджер подтверждает заявки и отзывает рядовых, но не руководителей.
  await act(3,'approve',5);
  assert.equal((await roleOf(5)).status,'approved');
  await assert.rejects(act(3,'revoke',2),/identity_forbidden/);
  await db.exec(`UPDATE employee_links SET role='manager' WHERE id='${link(6)}'`);
  await assert.rejects(act(3,'revoke',6),/identity_forbidden/);
  await act(3,'revoke',4);
  // Управляющий отзывает менеджера — роль снимается вместе с доступом.
  await act(2,'revoke',6);
  assert.deepEqual(await roleOf(6),{role:'staff',status:'revoked'});
  await assert.rejects(act(4,'approve',5),/identity_forbidden|identity_stale/);
  // Рядовой сотрудник ничего не может; приглашение создаёт и менеджер.
  await db.query('SELECT identity_action($1,$2,$3)',[3,'invite',JSON.stringify({hash:'h',restaurant_id:R})]);
  await assert.rejects(db.query('SELECT identity_action($1,$2,$3)',[5,'invite',JSON.stringify({hash:'h2',restaurant_id:R})]),/identity_forbidden/);
  // Управляющий в ресторане один.
  await assert.rejects(db.exec(`UPDATE employee_links SET role='director' WHERE id='${link(5)}'`),/duplicate|unique/);
  // План: владелец и управляющий да, менеджер нет.
  const plan=(actor,op)=>db.query('SELECT configure_categories($1,$2,$3,$4,$5,$6)',[actor,R,'2026-11',0,op,
    JSON.stringify([{id:'dddddddd-0000-0000-0000-000000000001',name:'Вино',unit:'amount',target:1000}])]);
  await assert.rejects(plan(3,'eeeeeeee-0000-0000-0000-000000000001'),/category_forbidden/);
  await plan(2,'eeeeeeee-0000-0000-0000-000000000002');
  assert.equal((await db.query('SELECT updated_by FROM category_months')).rows[0].updated_by,2);
  // Повторная заявка после отзыва приходит рядовым сотрудником.
  await db.exec(`UPDATE restaurants SET invite_hash='again' WHERE id='${R}'`);
  await db.query('SELECT identity_action($1,$2,$3)',[6,'request',JSON.stringify({hash:'again',id:link(7),name:'Имя 6',name_key:'имя 6x'})]);
  assert.equal((await db.query('SELECT role FROM employee_links WHERE user_id=6')).rows[0].role,'staff');
  console.log('v31 SQL passed: roles, one director, manager limits, director plans, revoke resets role.');
} finally { await db.close(); }
