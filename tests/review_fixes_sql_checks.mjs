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
} finally {
  await db.close();
}
