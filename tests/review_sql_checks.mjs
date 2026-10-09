import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  // This is the supported fresh-install order; v2-v6 are historical conversions.
  for(const name of ['schema.sql',...Array.from({length:20},(_,i)=>'migration_v'+(i+7)+'.sql')]){
    await db.exec(await source(name));
  }
  await db.exec('INSERT INTO users(id) VALUES(7),(8)');
  const key={kty:'RSA',alg:'RSA-OAEP-256',n:'test-device',e:'AQAB'};
  const insert=await db.query("INSERT INTO entries(user_id,kind,account,signed_amount,category,source_key) VALUES(7,'income','cash',500,'Чаевые','one') RETURNING id,revision");
  const id=insert.rows[0].id;
  await db.query('UPDATE entries SET signed_amount=900 WHERE id=$1',[id]);
  assert.equal((await db.query('SELECT revision FROM entries WHERE id=$1',[id])).rows[0].revision,2);
  const activate=revision=>db.query('SELECT activate_private_money_with_backups($1,$2,$3,$4,$5)',[
    7,[id],JSON.stringify([{id,revision}]),JSON.stringify(key),JSON.stringify([{record_id:String(id),payload:'ciphertext'}])]);
  await assert.rejects(activate(1),/private_entries_changed/);
  assert.equal(Number((await db.query('SELECT signed_amount FROM entries WHERE id=$1',[id])).rows[0].signed_amount),900);
  await activate(2);
  await assert.rejects(db.query("INSERT INTO entries(user_id,kind,account,signed_amount,category) VALUES(7,'income','cash',100,'Чаевые')"),/private/);

  const items=[1,2].map(i=>({kind:'expense',account:'cash',signed_amount:-100*i,category:'Еда',
    note:null,work_date:'2026-10-09',source_key:'telegram:8:20:'+i}));
  const add=rows=>db.query('SELECT add_money_entries($1,$2) value',[8,JSON.stringify(rows)]);
  await assert.rejects(add([items[0],{...items[1],signed_amount:0}]),/money_batch_invalid/);
  assert.equal((await db.query('SELECT count(*) count FROM entries WHERE user_id=8')).rows[0].count,0);

  const keyId=(await db.query("SELECT md5('test-device') value")).rows[0].value;
  await db.query('SELECT store_private_money_backup($1,$2,$3,$4)',[7,'telegram:7:22:0','sealed',keyId]);
  await db.query('SELECT store_private_money_backup($1,$2,$3,$4)',[7,'telegram:7:22:0','replayed',keyId]);
  assert.equal((await db.query("SELECT payload FROM private_money_backups WHERE record_id='telegram:7:22:0'")).rows[0].payload,'sealed');
  const localOp='00000000-0000-4000-8000-000000000040',cancelledOp='00000000-0000-4000-8000-000000000041';
  const restored=[{id:'local:'+localOp,source_key:'miniapp-expense:'+localOp,kind:'expense',
    account:'cash',signed_amount:-430,category:'Такси',work_date:'2026-10-09',created_at:'2026-10-09T12:00:00+03:00'}];
  await db.query('SELECT deactivate_private_money($1,$2,$3,$4,$5)',[7,
    '00000000-0000-4000-8000-000000000042',JSON.stringify(key),JSON.stringify(restored),
    JSON.stringify([String(id),'telegram:7:22:0','calendar:'+cancelledOp])]);
  assert.equal((await db.query("SELECT add_miniapp_expense(7,$1,430,'Такси','2026-10-09') value",[localOp])).rows[0].value.already_processed,true);
  const cancelledTip={kind:'income',account:'cash',signed_amount:500,category:'Чаевые',note:null,
    work_date:'2026-10-09',source_key:'calendar:'+cancelledOp};
  assert.deepEqual((await db.query('SELECT add_money_entries(7,$1) value',[JSON.stringify([cancelledTip])])).rows[0].value,[]);

  const expenseOperation='00000000-0000-4000-8000-000000000020';
  const expense=()=>db.query("SELECT add_miniapp_expense(8,$1,430,'Такси','2026-10-08') result",[expenseOperation]);
  const expenseId=(await expense()).rows[0].result.id;
  await db.query('UPDATE entries SET signed_amount=-450 WHERE id=$1',[expenseId]);
  assert.equal((await expense()).rows[0].result.id,expenseId);
  assert.equal(Number((await db.query('SELECT signed_amount FROM entries WHERE id=$1',[expenseId])).rows[0].signed_amount),-450);
  await db.query('DELETE FROM entries WHERE id=$1',[expenseId]);
  assert.equal((await expense()).rows[0].result.already_processed,true);
  assert.equal((await db.query('SELECT count(*) count FROM entries WHERE user_id=8')).rows[0].count,0);
  const saved=(await add(items)).rows[0].value;assert.equal(saved.length,2);
  await db.query("UPDATE entries SET account='card',signed_amount=-150 WHERE id=$1",[saved[0].id]);
  const replay=(await add(items)).rows[0].value;assert.equal(replay[0].signed_amount,-150);
  assert.equal(replay[0].account,'card');
  await assert.rejects(add([{...items[0],signed_amount:-999},items[1]]),/source_conflict/);
  assert.equal((await db.query('SELECT undo_money_batch($1,$2) n',[7,'telegram:8:20:'])).rows[0].n,0);
  assert.equal((await db.query('SELECT undo_money_batch($1,$2) n',[8,'telegram:8:20:'])).rows[0].n,2);
  assert.deepEqual((await add(items)).rows[0].value,[]);
  assert.equal((await db.query('SELECT count(*) count FROM entries WHERE user_id=8')).rows[0].count,0);

  const batch='00000000-0000-4000-8000-000000000001',token='00000000-0000-4000-8000-000000000002';
  await db.query('SELECT store_telegram_update($1,$2,$3,$4)',[1,'actor','encrypted','a'.repeat(64)]);
  await db.query('SELECT store_telegram_update($1,$2,$3,$4)',[2,'actor','encrypted','b'.repeat(64)]);
  assert.equal((await db.query('SELECT * FROM claim_telegram_batch($1,$2,$3)',[[2],batch,token])).rows.length,0);
  assert.equal((await db.query('SELECT * FROM claim_telegram_batch($1,$2,$3)',[[1,2],batch,token])).rows.length,2);
  assert.equal((await db.query('SELECT remember_telegram_context($1,$2,$3) value',[1,token,'sealed-dialog'])).rows[0].value,'sealed-dialog');
  assert.equal((await db.query('SELECT remember_telegram_context($1,$2,$3) value',[1,token,'different'])).rows[0].value,'sealed-dialog');
  await assert.rejects(db.query('SELECT save_telegram_actor_state($1,$2,$3)',[1,batch,'sealed-state']),/telegram_claim_changed/);
  await db.query('SELECT save_telegram_actor_state($1,$2,$3)',[1,token,'sealed-state']);
  assert.equal((await db.query("SELECT payload FROM telegram_actor_state WHERE actor_key='actor'")).rows[0].payload,'sealed-state');
  assert.equal((await db.query('SELECT * FROM claim_telegram_batch($1,$2,$3)',[[1,2],batch,token])).rows.length,0);
  await db.query("UPDATE telegram_inbox SET claimed_until=now()-interval '1 second'");
  assert.equal((await db.query('SELECT * FROM claim_telegram_batch($1,$2,$3)',[[1,2],batch,token])).rows.length,2);
  await db.query('SELECT finish_telegram_batch($1,$2,true)',[batch,token]);
  assert.equal((await db.query('SELECT payload FROM telegram_inbox WHERE update_id=1')).rows[0].payload,null);
  assert.equal((await db.query('SELECT execution_context FROM telegram_inbox WHERE update_id=1')).rows[0].execution_context,null);

  await db.query('SELECT store_telegram_update($1,$2,$3,$4)',[3,'blocked','encrypted','d'.repeat(64)]);
  await db.query('SELECT store_telegram_update($1,$2,$3,$4)',[4,'ready','encrypted','e'.repeat(64)]);
  await db.exec("UPDATE telegram_inbox SET next_attempt_at=now()+interval '1 minute' WHERE update_id=3");
  assert.deepEqual((await db.query('SELECT update_id FROM ready_telegram_heads()')).rows.map(r=>r.update_id),[4]);
  await db.query('SELECT store_telegram_update($1,$2,$3,$4)',[1,'actor','different','c'.repeat(64)]);
  assert.equal((await db.query('SELECT payload FROM telegram_inbox WHERE update_id=1')).rows[0].payload,null);

  const shift=(await db.query("INSERT INTO shifts(user_id,shift_date,starts_at,ends_at) VALUES(8,'2026-10-09','10:00','22:00') RETURNING id")).rows[0].id;
  assert.equal((await db.query('SELECT claim_shift_notice($1,$2,$3) ok',[shift,'start',token])).rows[0].ok,true);
  assert.equal((await db.query('SELECT start_reminder_sent FROM shifts WHERE id=$1',[shift])).rows[0].start_reminder_sent,false);
  assert.equal((await db.query('SELECT claim_shift_notice($1,$2,$3) ok',[shift,'start',batch])).rows[0].ok,false);
  await db.query('SELECT finish_shift_notice($1,$2,$3,false)',[shift,'start',token]);
  assert.equal((await db.query('SELECT claim_shift_notice($1,$2,$3) ok',[shift,'start',batch])).rows[0].ok,true);
  await db.query('SELECT finish_shift_notice($1,$2,$3,true)',[shift,'start',batch]);
  assert.equal((await db.query('SELECT start_reminder_sent FROM shifts WHERE id=$1',[shift])).rows[0].start_reminder_sent,true);
  assert.equal((await db.query('SELECT claim_shift_notice($1,$2,$3) ok',[shift,'end',token])).rows[0].ok,true);
  await db.query("UPDATE shifts SET notice_claims=jsonb_set(notice_claims,'{end,until}',to_jsonb((now()-interval '1 second')::TEXT)) WHERE id=$1",[shift]);
  assert.equal((await db.query('SELECT claim_shift_notice($1,$2,$3) ok',[shift,'end',batch])).rows[0].ok,true);
  await db.query('SELECT finish_shift_notice($1,$2,$3,true)',[shift,'end',token]);
  assert.equal((await db.query('SELECT time_prompt_sent FROM shifts WHERE id=$1',[shift])).rows[0].time_prompt_sent,false);
  await db.query('SELECT finish_shift_notice($1,$2,$3,true)',[shift,'end',batch]);
  assert.equal((await db.query('SELECT time_prompt_sent FROM shifts WHERE id=$1',[shift])).rows[0].time_prompt_sent,true);

  const sid=(await db.query('SELECT id FROM research_subjects WHERE user_id=8')).rows[0].id;
  await db.query("INSERT INTO analytics_events(subject_id,event,source,occurred_at,onboarding_version,app_version) VALUES($1,'user_started','bot','2026-08-01T12:00:00+03:00',1,'test')",[sid]);
  await db.query("INSERT INTO analytics_events(subject_id,event,source,occurred_at,onboarding_version,app_version) VALUES($1,'user_started','bot','2026-10-09T12:00:00+03:00',1,'test')",[sid]);
  assert.equal(new Date((await db.query('SELECT first_started_at FROM research_subjects WHERE id=$1',[sid])).rows[0].first_started_at).toISOString().slice(0,10),'2026-08-01');
  assert.equal((await db.query("SELECT has_table_privilege('anon','telegram_inbox','SELECT') ok")).rows[0].ok,false);
  await db.exec('SET ROLE anon');
  for (const table of ['users','entries','shifts','money_source_receipts','telegram_inbox','telegram_actor_state']) {
    await assert.rejects(db.query('SELECT * FROM '+table),/permission denied/);
  }
  await assert.rejects(db.query("SELECT add_money_entries(8,'[]')"),/permission denied/);
  await db.exec('RESET ROLE; SET ROLE service_role');
  const item={...items[0],source_key:'telegram:8:21:0'};
  assert.equal((await add([item])).rows[0].value.length,1);
  await db.exec('RESET ROLE');
  await db.query('SELECT store_telegram_update($1,$2,$3,$4)',[50,'clear-actor','encrypted','f'.repeat(64)]);
  await db.query("INSERT INTO entries(user_id,kind,account,signed_amount,category,source_key) VALUES(8,'income','cash',100,'Чаевые','clear-original')");
  assert.equal((await db.query("SELECT apply_telegram_user_action(8,50,'clear') value")).rows[0].value,true);
  await db.query("INSERT INTO entries(user_id,kind,account,signed_amount,category,source_key) VALUES(8,'income','cash',200,'Чаевые','clear-new')");
  assert.equal((await db.query("SELECT apply_telegram_user_action(8,50,'clear') value")).rows[0].value,false);
  assert.equal(Number((await db.query('SELECT sum(signed_amount) value FROM entries WHERE user_id=8')).rows[0].value),200);
  await db.query('SELECT store_telegram_update($1,$2,$3,$4)',[51,'delete-actor','encrypted','g'.repeat(64)]);
  assert.equal((await db.query("SELECT apply_telegram_user_action(8,51,'delete') value")).rows[0].value,true);
  await db.query('INSERT INTO users(id) VALUES(8)');
  await db.query("INSERT INTO entries(user_id,kind,account,signed_amount,category,source_key) VALUES(8,'income','cash',300,'Чаевые','after-delete')");
  assert.equal((await db.query("SELECT apply_telegram_user_action(8,51,'delete') value")).rows[0].value,false);
  assert.equal(Number((await db.query('SELECT sum(signed_amount) value FROM entries WHERE user_id=8')).rows[0].value),300);
  console.log('Fresh schema, versioned transfer, atomic batches, inbox recovery, reminder claims, cohort anchor and permissions: OK');
} finally {await db.close();}
