// v41: исправления из ревью 15.11.2026 на полной цепочке миграций.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const exec=(sql,...args)=>db.query(sql,args);
const R='aaaaaaaa-0000-0000-0000-000000000001';
const OLD='cccccccc-0000-0000-0000-000000000001';
const NEW='cccccccc-0000-0000-0000-000000000002';
const P='dddddddd-0000-0000-0000-000000000001';
const D='dddddddd-0000-0000-0000-000000000002';
const call=async(actor,action,args)=>(await exec('SELECT identity_action($1,$2,$3) AS r',actor,action,JSON.stringify(args))).rows[0].r;
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  await db.exec(await source('schema.sql'));
  for(let v=7;v<=40;v++)await db.exec(await source(`migration_v${v}.sql`));
  await db.exec('INSERT INTO users(id) VALUES(1),(2),(3)');
  await exec("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Тест',1)",R);
  await exec("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES($1,$2,2,'Аня','аня','approved')",OLD,R);
  const draft=JSON.stringify({people:[{name:'Аня',link:OLD}]});
  await exec("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft,published,published_at) VALUES($1,$2,'2026-11-16','2026-11-30',$3,$3,now())",P,R,draft);
  await exec("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft) VALUES($1,$2,'2026-12-01','2026-12-15',$3)",D,R,draft);
  await exec("INSERT INTO day_off_requests(link_id,restaurant_id,start_date,end_date,days,roster_id) VALUES($1,$2,'2026-11-16','2026-11-30','[\"2026-11-18\"]',$3)",OLD,R,P);
  await exec("INSERT INTO weekday_wishes(link_id,restaurant_id,days) VALUES($1,$2,'[1]')",OLD,R);

  await db.exec(await source('migration_v41.sql'));

  // 1. Опубликованное до миграции считается разложенным; черновик без публикации — нет.
  let q=await exec('SELECT id,shifts_synced_at=published_at AS same,shifts_synced_at FROM rosters ORDER BY start_date');
  assert.equal(q.rows[0].same,true);
  assert.equal(q.rows[1].shifts_synced_at,null);

  // 2. Отозванный сотрудник подаёт заявку снова: раньше смена id падала на внешних ключах (23503).
  await call(1,'invite',{hash:'h'});
  await call(1,'revoke',{id:OLD});
  const again=await call(2,'request',{hash:'h',id:NEW,name:'Аня',name_key:'аня'});
  assert.equal(again.id,NEW);
  assert.equal(again.status,'pending');
  q=await exec('SELECT link_id FROM day_off_requests');assert.deepEqual(q.rows.map(r=>r.link_id),[NEW]);
  q=await exec('SELECT link_id FROM weekday_wishes');assert.deepEqual(q.rows.map(r=>r.link_id),[NEW]);
  q=await exec('SELECT draft,published FROM rosters ORDER BY start_date');
  for(const row of q.rows){
    assert.equal(row.draft.people[0].link,NEW);
    if(row.published)assert.equal(row.published.people[0].link,NEW);
  }
  // Старая кнопка подтверждения по прежнему id не срабатывает, новая — да.
  await assert.rejects(call(1,'approve',{id:OLD}),/identity_stale|identity_forbidden/);
  assert.equal((await call(1,'approve',{id:NEW})).status,'approved');

  // 3. Очередь: безнадёжное сообщение откладывается только после 10 попыток и 30 минут.
  const B='bbbbbbbb-0000-0000-0000-000000000001', T='bbbbbbbb-0000-0000-0000-000000000002';
  const fail=async(id,attempts,age)=>{
    await exec(`INSERT INTO telegram_inbox(update_id,actor_key,payload,payload_hash,received_at,attempts)
                VALUES($1,$2,'sealed',$3,now()-$4::interval,$5)`,id,'actor'+id,'a'.repeat(64),age,attempts);
    const claimed=await exec('SELECT * FROM claim_telegram_batch($1,$2,$3)',[id],B.slice(0,-1)+id,T.slice(0,-1)+id);
    assert.equal(claimed.rows.length,1);
    return (await exec('SELECT finish_telegram_batch($1,$2,false) AS n',B.slice(0,-1)+id,T.slice(0,-1)+id)).rows[0].n;
  };
  assert.equal(await fail(1,9,'1 hour'),1);       // десятая попытка, час спустя — отложено
  assert.equal(await fail(2,3,'1 hour'),0);       // мало попыток — ещё повторяем
  assert.equal(await fail(3,40,'5 minutes'),0);   // короткий сбой базы: много попыток, но мало времени
  q=await exec('SELECT update_id,finished_at IS NOT NULL AS done,abandoned_at IS NOT NULL AS abandoned,payload FROM telegram_inbox ORDER BY update_id');
  assert.deepEqual(q.rows,[{update_id:1,done:true,abandoned:true,payload:null},
                           {update_id:2,done:false,abandoned:false,payload:'sealed'},
                           {update_id:3,done:false,abandoned:false,payload:'sealed'}]);
  // Успешное завершение по-прежнему возвращает 0 и стирает содержимое.
  await exec("UPDATE telegram_inbox SET next_attempt_at=now() WHERE update_id=2");
  await exec('SELECT * FROM claim_telegram_batch($1,$2,$3)',[2],B,T);
  assert.equal((await exec('SELECT finish_telegram_batch($1,$2,true) AS n',B,T)).rows[0].n,0);
  for(const role of ['anon','authenticated']){
    await db.exec('SET ROLE '+role);
    await assert.rejects(exec('SELECT finish_telegram_batch($1,$2,true)',B,T),/permission denied/);
    await db.exec('RESET ROLE');
  }
  console.log('v41 SQL passed: published rosters marked synced, rejoin keeps wishes and roster links, stale buttons, inbox gives up after 10 attempts and 30 minutes.');

  // ─── v42 ───
  await db.exec(await source('migration_v42.sql'));
  // 1. Напоминание о пожеланиях, которое не отправилось, повторяется через час, не больше трёх попыток.
  await exec("UPDATE rosters SET collect_wishes_at='2026-11-20T08:00:00Z' WHERE id=$1",D);
  const claim=async at=>(await exec('SELECT claim_roster_wish_notice($1,NULL) AS c',at)).rows[0].c;
  const first=await claim('2026-11-25T09:00:00Z');
  assert.equal(first.link_id,NEW);assert.equal(first.stage,1);
  assert.equal(await claim('2026-11-25T09:30:00Z'),null);            // раньше часа — не повторяем
  assert.equal((await claim('2026-11-25T10:01:00Z')).stage,1);        // вторая попытка
  assert.equal((await claim('2026-11-25T11:02:00Z')).stage,1);        // третья
  assert.equal(await claim('2026-11-25T12:03:00Z'),null);             // больше трёх — нет
  q=await exec('SELECT attempts,sent_at FROM roster_wish_notices WHERE roster_id=$1 AND stage=1',D);
  assert.deepEqual(q.rows,[{attempts:3,sent_at:null}]);
  // Отправленное вовремя не повторяется, второй этап — через 48 часов.
  await exec("UPDATE roster_wish_notices SET sent_at='2026-11-25T11:03:00Z' WHERE roster_id=$1",D);
  assert.equal(await claim('2026-11-26T11:00:00Z'),null);
  assert.equal((await claim('2026-11-27T12:00:00Z')).stage,2);

  // 2. Рассылка: одна отметка на человека и подтверждение.
  await exec('INSERT INTO broadcast_deliveries(update_id,user_id) VALUES(77,2)');
  await assert.rejects(exec('INSERT INTO broadcast_deliveries(update_id,user_id) VALUES(77,2)'),/duplicate key/);
  for(const role of ['anon','authenticated']){
    await db.exec('SET ROLE '+role);
    await assert.rejects(exec('SELECT * FROM broadcast_deliveries'),/permission denied/);
    await db.exec('RESET ROLE');
  }

  // 3. Перенос: управляющий не уходит туда, где управляющий есть; пожелания едут с человеком.
  const R2='aaaaaaaa-0000-0000-0000-000000000002';
  await exec("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Второй',1)",R2);
  await exec("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status,role) VALUES('cccccccc-0000-0000-0000-000000000009',$1,3,'Боря','боря','approved','director')",R2);
  await exec("UPDATE employee_links SET role='director' WHERE id=$1",NEW);
  const transfer=()=>exec('SELECT identity_transfer(1,$1,$2,$3) AS r',NEW,R,R2);
  await assert.rejects(transfer(),/identity_director_taken/);
  await exec("UPDATE employee_links SET role='staff' WHERE id=$1",NEW);
  assert.equal((await transfer()).rows[0].r.restaurant_id,R2);
  q=await exec('SELECT restaurant_id FROM weekday_wishes WHERE link_id=$1',NEW);
  assert.deepEqual(q.rows,[{restaurant_id:R2}]);
  q=await exec('SELECT restaurant_id FROM day_off_requests WHERE link_id=$1',NEW);
  assert.deepEqual(q.rows,[{restaurant_id:R}]);   // ответ на график прежнего ресторана остаётся при нём

  // 4. Графики одного ресторана не пересекаются; соседние и чужие — можно.
  const roster=(id,place,start,end)=>exec("INSERT INTO rosters(id,restaurant_id,start_date,end_date,draft) VALUES($1,$2,$3,$4,'{}')",id,place,start,end);
  await assert.rejects(roster('dddddddd-0000-0000-0000-000000000003',R,'2026-11-20','2026-11-25'),/roster_overlap/);
  await roster('dddddddd-0000-0000-0000-000000000004',R,'2026-12-16','2026-12-20');
  await roster('dddddddd-0000-0000-0000-000000000005',R2,'2026-11-20','2026-11-25');
  await assert.rejects(exec("UPDATE rosters SET end_date='2026-12-18' WHERE id=$1",D),/roster_overlap/);
  await exec("UPDATE rosters SET draft='{\"people\":[]}' WHERE id=$1",D);   // правка без смены дат не проверяется заново
  console.log('v42 SQL passed: wish reminder retries, broadcast deliveries, transfer carries wishes and guards director, roster periods never overlap.');
} finally {
  await db.close();
}
