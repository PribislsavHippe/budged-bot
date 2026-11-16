// v46: тренинги в общем чате — один чат на ресторан, сообщения и записи только для сервера, каскад при удалении.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const R='aaaaaaaa-0000-0000-0000-000000000001', Q='aaaaaaaa-0000-0000-0000-000000000002';
const A='cccccccc-0000-0000-0000-000000000001';
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:40},(_,i)=>'migration_v'+(i+7)+'.sql')]) await db.exec(await source(name));
  await db.exec('INSERT INTO users(id) VALUES(1),(2),(3)');
  await db.query("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Тест',1),($2,'Другой',3)",[R,Q]);
  await db.query("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES($1,$2,2,'Аля','аля','approved')",[A,R]);
  await db.exec('SET ROLE service_role');
  await db.query("INSERT INTO training_chats(restaurant_id,chat_id,sheet_id,label) VALUES($1,-100,'10miCtSWFJadfLBS0qERYwKc','Итали')",[R]);
  await assert.rejects(db.query("INSERT INTO training_chats(restaurant_id,chat_id) VALUES($1,-100)",[Q]),/unique/);
  await assert.rejects(db.query("INSERT INTO training_chats(restaurant_id,chat_id) VALUES($1,7)",[Q]),/check/);
  await assert.rejects(db.query("INSERT INTO training_chats(restaurant_id,chat_id,sheet_id) VALUES($1,-7,'../x')",[Q]),/check/);
  assert.equal((await db.query('SELECT position FROM training_chats')).rows[0].position,'официант');
  const K='2026-11-18|720|сторителлинг';
  await db.query("INSERT INTO training_sessions(restaurant_id,session_key,short_id,session) VALUES($1,$2,'0123456789','{}')",[R,K]);
  await assert.rejects(db.query("INSERT INTO training_sessions(restaurant_id,session_key,short_id,session) VALUES($1,'k2','0123456789','{}')",[R]),/unique/);
  await assert.rejects(db.query("INSERT INTO training_sessions(restaurant_id,session_key,short_id,session) VALUES($1,'k3','XYZ','{}')",[R]),/check/);
  await assert.rejects(db.query("INSERT INTO training_invites(restaurant_id,session_key,link_id) VALUES($1,'нет такого',$2)",[R,A]),/foreign key/);
  await db.query("INSERT INTO training_invites(restaurant_id,session_key,link_id) VALUES($1,$2,$3)",[R,K,A]);
  await db.query("INSERT INTO training_signups(restaurant_id,session_key,link_id) VALUES($1,$2,$3)",[R,K,A]);
  await assert.rejects(db.query("INSERT INTO training_signups(restaurant_id,session_key,link_id) VALUES($1,$2,$3)",[R,K,A]),/duplicate|unique/);
  await db.exec('RESET ROLE; SET ROLE anon');
  for(const table of ['training_chats','training_sessions','training_invites','training_signups'])
    await assert.rejects(db.query('SELECT * FROM '+table),/permission/);
  await db.exec('RESET ROLE');
  await db.query('DELETE FROM restaurants WHERE id=$1',[R]);
  for(const table of ['training_sessions','training_invites','training_signups'])
    assert.equal((await db.query('SELECT count(*)::int AS n FROM '+table)).rows[0].n,0,table);
  console.log('v46 trainings: one chat per restaurant, group chats only, strict sheet id, short ids, invites and sign-ups tied to sessions, no public access, cascade.');
} finally {await db.close();}
