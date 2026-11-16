// Run: PGLITE_PATH=/path/to/node_modules/@electric-sql/pglite/dist/index.js node tests/research_sql_checks.mjs
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
await db.exec(`CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;
CREATE TABLE users(id BIGINT PRIMARY KEY); INSERT INTO users VALUES(1);`);
await db.exec(await fs.readFile(new URL('../migration_v9.sql',import.meta.url),'utf8'));
await db.exec(await fs.readFile(new URL('../migration_v10.sql',import.meta.url),'utf8'));
await db.exec(await fs.readFile(new URL('../migration_v16.sql',import.meta.url),'utf8'));
await db.exec(await fs.readFile(new URL('../migration_v18.sql',import.meta.url),'utf8'));
await db.exec(await fs.readFile(new URL('../migration_v34.sql',import.meta.url),'utf8'));
let q=await db.query('SELECT cohort,onboarding_state,onboarding_version FROM research_subjects WHERE user_id=1');
assert.deepEqual(q.rows,[{cohort:'existing',onboarding_state:'legacy',onboarding_version:0}]);
await db.exec('INSERT INTO users VALUES(2),(3),(4)');
const record=async(actor,kind,op=null)=>db.query(`SELECT record_ux_event($1,$2,'bot','chat',NULL,NULL,'test',$3) AS r`,[actor,kind,op]);
await record(2,'user_started');await record(2,'user_started');
await record(2,'onboarding_started');
await Promise.all([record(2,'tip_added','same'),record(2,'tip_added','same')]);
q=await db.query(`SELECT event,count(*)::int n FROM analytics_events e JOIN research_subjects s ON s.id=e.subject_id WHERE s.user_id=2 GROUP BY event`);
const counts=Object.fromEntries(q.rows.map(r=>[r.event,r.n]));
for(const kind of ['user_started','onboarding_started','tip_added','first_tip_added','first_value_action','onboarding_completed'])assert.equal(counts[kind],1,kind);
q=await db.query('SELECT onboarding_state FROM research_subjects WHERE user_id=2');assert.equal(q.rows[0].onboarding_state,'completed');
await record(3,'onboarding_started');await record(3,'onboarding_skipped');await record(3,'expense_added');
q=await db.query(`SELECT count(*)::int n FROM analytics_events e JOIN research_subjects s ON s.id=e.subject_id WHERE s.user_id=3 AND event='onboarding_completed'`);assert.equal(q.rows[0].n,0);
await record(1,'onboarding_started');q=await db.query('SELECT onboarding_state FROM research_subjects WHERE user_id=1');assert.equal(q.rows[0].onboarding_state,'legacy');
await record(4,'onboarding_started');await record(4,'shift_planned');
q=await db.query(`SELECT event FROM analytics_events e JOIN research_subjects s ON s.id=e.subject_id WHERE s.user_id=4`);
assert.deepEqual(new Set(q.rows.map(r=>r.event)),new Set(['onboarding_started','shift_planned','first_value_action','onboarding_completed']));
for(const kind of ['schedule_import_started','schedule_previewed','schedule_imported'])await record(4,kind);
q=await db.query(`SELECT event FROM analytics_events e JOIN research_subjects s ON s.id=e.subject_id WHERE s.user_id=4 AND event LIKE 'schedule_%'`);
assert.deepEqual(new Set(q.rows.map(r=>r.event)),new Set(['schedule_import_started','schedule_previewed','schedule_imported']));
for(const role of ['anon','authenticated']){
  await db.exec('SET ROLE '+role);
  for(const table of ['research_subjects','analytics_events','feedback','restaurants'])await assert.rejects(db.query('SELECT * FROM '+table),/permission denied/);
  await assert.rejects(record(2,'tip_added'),/permission denied/);await db.exec('RESET ROLE');
}
await db.exec('SET ROLE service_role');await record(2,'shift_closed');await db.exec('RESET ROLE');
await assert.rejects(record(2,'financial_amount'),/check constraint/);
// v34: шаги графика смен и экран «schedule»; содержимого в событии нет.
await db.exec('SET ROLE service_role');
for(const kind of ['roster_created','roster_variant_picked','roster_built','roster_dates_changed','roster_published','roster_republished','days_off_asked','days_off_answered','roster_viewed'])
  await db.query(`SELECT record_ux_event($1,$2,'server','schedule',NULL,NULL,'test',NULL)`,[2,kind]);
await db.query(`SELECT record_ux_event($1,'roster_viewed','server','schedule',NULL,NULL,'test','11111111-1111-1111-1111-111111111111')`,[2]);
await db.query(`SELECT record_ux_event($1,'roster_viewed','server','schedule',NULL,NULL,'test','11111111-1111-1111-1111-111111111111')`,[2]);
q=await db.query(`SELECT count(*)::int n FROM analytics_events e JOIN research_subjects s ON s.id=e.subject_id WHERE s.user_id=2 AND e.event='roster_viewed' AND e.screen='schedule'`);
assert.equal(q.rows[0].n,2); // одна запись без ключа и одна с ключом дня: повтор того же дня не дублируется
await db.exec('RESET ROLE');
// v40: напоминания, «не понял», исправления, блокировка и подсчёт смен без Telegram ID наружу.
// Таблицы смен и роль — в том виде, как их создают v31 и v12/schema.sql.
await db.exec(`ALTER TABLE employee_links ADD COLUMN role TEXT NOT NULL DEFAULT 'staff' CHECK(role IN ('staff','manager','director'));
CREATE TABLE shifts(id BIGSERIAL PRIMARY KEY,user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,shift_date DATE NOT NULL,UNIQUE(user_id,shift_date));
CREATE TABLE worked_shifts(user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,shift_date DATE NOT NULL,PRIMARY KEY(user_id,shift_date));
REVOKE ALL ON shifts,worked_shifts FROM anon,authenticated;GRANT ALL ON shifts,worked_shifts TO service_role;`);
await db.exec(await fs.readFile(new URL('../migration_v40.sql',import.meta.url),'utf8'));
await db.exec('SET ROLE service_role');
const step=async(kind,detail)=>db.query(`SELECT record_ux_event($1,$2,'server','chat',$3,NULL,'test',NULL)`,[2,kind,detail]);
for(const [kind,detail] of [['reminder_sent','shift_tomorrow'],['reminder_sent','shift_end'],['reminder_sent','days_off'],
  ['message_not_understood','no_amount'],['message_not_understood','ambiguous_amount'],['message_not_understood','short_number'],
  ['message_not_understood','unclear'],['message_not_understood','bank_no_amount'],['entry_corrected','undo'],
  ['entry_corrected','account'],['entry_corrected','amount'],['entry_corrected','delete'],['tip_added','bank'],
  ['tip_added','message'],['bot_blocked',null],['calendar_synced',null],['private_mode_enabled',null],
  ['private_mode_disabled',null],['sale_saved',null],['sales_plan_saved',null],['onboarding_step','tip']])await step(kind,detail);
await assert.rejects(step('reminder_sent','23:30'),/check constraint/);
await assert.rejects(step('message_not_understood','чай 500'),/check constraint/);
await db.exec('RESET ROLE');
await db.exec(`INSERT INTO users VALUES(5),(6),(7);
INSERT INTO restaurants(id,name,owner_id) VALUES('00000000-0000-0000-0000-0000000000a1','Ресторан',5);
INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status,role) VALUES
 ('00000000-0000-0000-0000-0000000000b1','00000000-0000-0000-0000-0000000000a1',6,'Аня','аня','approved','manager'),
 ('00000000-0000-0000-0000-0000000000b2','00000000-0000-0000-0000-0000000000a1',7,'Боря','боря','pending','staff');
INSERT INTO shifts(user_id,shift_date) VALUES(6,'2026-11-10'),(6,'2026-11-12'),(6,'2026-10-01');
INSERT INTO worked_shifts(user_id,shift_date) VALUES(6,'2026-11-12');`);
await db.exec('SET ROLE service_role');
q=await db.query(`SELECT * FROM research_people('2026-11-01','2026-11-15')`);
await db.exec('RESET ROLE');
const people=Object.fromEntries(q.rows.map(r=>[r.subject_id,r]));
const sid=async uid=>(await db.query('SELECT id FROM research_subjects WHERE user_id=$1',[uid])).rows[0].id;
assert.equal(people[await sid(5)].role,'owner');
assert.equal(people[await sid(6)].role,'manager');
assert.equal(people[await sid(7)].role,'pending');
assert.equal(people[await sid(3)].role,'none');
assert.equal(people[await sid(6)].planned.length,2);   // октябрьская смена вне периода
assert.equal(people[await sid(6)].worked.length,1);
assert.ok(!Object.keys(q.rows[0]).includes('user_id'));
for(const role of ['anon','authenticated']){
  await db.exec('SET ROLE '+role);
  await assert.rejects(db.query(`SELECT * FROM research_people('2026-11-01','2026-11-15')`),/permission denied/);
  await db.exec('RESET ROLE');
}
await db.exec(`INSERT INTO feedback(subject_id,category,body,screen,app_version) SELECT id,'idea','Test only','help','test' FROM research_subjects WHERE user_id=2`);
await db.exec('DELETE FROM users WHERE id=2');
q=await db.query('SELECT count(*)::int n FROM feedback');assert.equal(q.rows[0].n,0);
console.log('Research SQL checks passed: v18, v34 schedule steps, v40 habit events and research_people, baseline, milestones, dedupe, skip, service role, RLS, cascade deletion.');
await db.close();
