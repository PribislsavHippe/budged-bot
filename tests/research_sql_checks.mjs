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
for(const role of ['anon','authenticated']){
  await db.exec('SET ROLE '+role);
  for(const table of ['research_subjects','analytics_events','feedback','restaurants'])await assert.rejects(db.query('SELECT * FROM '+table),/permission denied/);
  await assert.rejects(record(2,'tip_added'),/permission denied/);await db.exec('RESET ROLE');
}
await db.exec('SET ROLE service_role');await record(2,'shift_closed');await db.exec('RESET ROLE');
await assert.rejects(record(2,'financial_amount'),/check constraint/);
await db.exec(`INSERT INTO feedback(subject_id,category,body,screen,app_version) SELECT id,'idea','Test only','help','test' FROM research_subjects WHERE user_id=2`);
await db.exec('DELETE FROM users WHERE id=2');
q=await db.query('SELECT count(*)::int n FROM feedback');assert.equal(q.rows[0].n,0);
console.log('Research SQL checks passed: v16, baseline, milestones, dedupe, skip, service role, RLS, cascade deletion.');
await db.close();
