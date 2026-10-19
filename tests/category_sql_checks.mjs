import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
const db=new PGlite();
const r='11111111-1111-4111-8111-111111111111',r2='22222222-2222-4222-8222-222222222222';
const c='33333333-3333-4333-8333-333333333333',other='44444444-4444-4444-8444-444444444444';
await db.exec(`CREATE ROLE anon;CREATE ROLE authenticated;CREATE ROLE service_role BYPASSRLS;
CREATE TABLE users(id BIGINT PRIMARY KEY);INSERT INTO users VALUES(1),(2),(3);
CREATE TABLE restaurants(id UUID PRIMARY KEY,owner_id BIGINT,name TEXT DEFAULT 'Тест');INSERT INTO restaurants(id,owner_id) VALUES('${r}',1),('${r2}',3);
CREATE TABLE employee_links(user_id BIGINT,restaurant_id UUID,status TEXT);INSERT INTO employee_links VALUES(2,'${r}','approved');`);
await db.exec(await fs.readFile(new URL('../migration_v27.sql',import.meta.url),'utf8'));
const definitions=[{id:c,name:'Гарниры',unit:'amount'},{id:other,name:'Шоты',unit:'count'}];
const configure=async(actor,restaurant,month,expected,operation,defs=definitions)=>db.query('SELECT configure_categories($1,$2,$3,$4,$5,$6)',[actor,restaurant,month,expected,operation,JSON.stringify(defs)]);
const op='55555555-5555-4555-8555-555555555555';
await configure(1,r,'2026-10',0,op);await configure(1,r,'2026-10',0,op);
assert.equal((await db.query('SELECT revision FROM category_months')).rows[0].revision,1);
await assert.rejects(configure(2,r,'2026-10',1,'66666666-6666-4666-8666-666666666666'),/category_forbidden/);
await assert.rejects(configure(1,r2,'2026-10',0,op),/category_forbidden/);
const save=async(actor,rest,revision,operation,metrics)=>db.query('SELECT save_category_report($1,$2,$3,$4,$5,$6)',[actor,rest,'2026-10',revision,operation,JSON.stringify(metrics)]);
const metrics={[c]:{value:1200.5,cutoff:'2026-10-14'},[other]:{value:0,cutoff:'2026-10-14'}};
const saved='77777777-7777-4777-8777-777777777777';
await save(2,r,1,saved,metrics);await save(2,r,1,saved,metrics);
assert.equal((await db.query('SELECT count(*)::int n FROM category_reports')).rows[0].n,1);
await assert.rejects(save(3,r,1,saved,metrics),/category_forbidden/);
await assert.rejects(save(2,r,1,'88888888-8888-4888-8888-888888888888',{[c]:{value:10,cutoff:'2026-09-30'}}),/category_invalid_date/);
await assert.rejects(save(2,r,1,'88888888-8888-4888-8888-888888888888',{[other]:{value:1.5,cutoff:'2026-10-14'}}),/category_invalid/);
await assert.rejects(save(2,r,1,'88888888-8888-4888-8888-888888888888',{['99999999-9999-4999-8999-999999999999']:{value:1,cutoff:'2026-10-14'}}),/category_invalid/);
await configure(1,r,'2026-10',1,'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',[{id:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',name:'Хлеб',unit:'amount'}]);
await save(2,r,1,saved,metrics); // Retry successful historical save after config change.
await assert.rejects(save(2,r,1,'88888888-8888-4888-8888-888888888888',metrics),/category_stale/);
assert.equal((await db.query('SELECT definitions FROM category_reports')).rows[0].definitions[0].name,'Гарниры');
for(const role of ['anon','authenticated']){
 await db.exec('SET ROLE '+role);
 await assert.rejects(db.query('SELECT * FROM category_months'),/permission denied/);
 await assert.rejects(db.query('SELECT * FROM category_reports'),/permission denied/);
 await assert.rejects(configure(1,r,'2026-10',2,op),/permission denied/);
 await assert.rejects(save(2,r,2,saved,metrics),/permission denied/);
 await db.exec('RESET ROLE');
}
const unknown='cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const observed=[{id:unknown,name:'Новые закуски',unit:'number'}];
const observation={[unknown]:{value:1450,cutoff:'2026-10-14'}};
const discover=async(actor,restaurant,op,defs=observed,values=observation)=>db.query('SELECT save_discovered_category_report($1,$2,$3,$4,$5,$6)',[actor,restaurant,'2026-10',op,JSON.stringify(defs),JSON.stringify(values)]);
const newOp='dddddddd-dddd-4ddd-8ddd-dddddddddddd';
await discover(2,r,newOp);await discover(2,r,newOp);
await discover(1,r,'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee');
assert.equal((await db.query('SELECT count(*)::int n FROM category_alerts')).rows[0].n,1);
assert.equal((await db.query('SELECT definitions FROM category_months')).rows[0].definitions[0].name,'Хлеб');
await assert.rejects(discover(3,r,'ffffffff-ffff-4fff-8fff-ffffffffffff'),/category_forbidden/);
await discover(3,null,'ffffffff-ffff-4fff-8fff-ffffffffffff'); // Personal bot still works without restaurant approval.
const firstClaim=await db.query("SELECT * FROM claim_category_alerts('11111111-aaaa-4aaa-8aaa-111111111111')");
assert.equal(firstClaim.rows.length,2);
assert.equal(firstClaim.rows[0].category_name,'Новые закуски');
assert.equal((await db.query("SELECT * FROM claim_category_alerts('22222222-aaaa-4aaa-8aaa-222222222222')")).rows.length,0);
await db.exec("UPDATE category_alerts SET claimed_until=clock_timestamp()-interval '1 second'");
assert.equal((await db.query("SELECT * FROM claim_category_alerts('22222222-aaaa-4aaa-8aaa-222222222222')")).rows.length,2);
await db.exec('UPDATE category_alerts SET notified_at=clock_timestamp()');
assert.equal((await db.query("SELECT * FROM claim_category_alerts('33333333-aaaa-4aaa-8aaa-333333333333')")).rows.length,0);
for(const role of ['anon','authenticated']){
 await db.exec('SET ROLE '+role);
 await assert.rejects(discover(2,r,newOp),/permission denied/);
 await assert.rejects(db.query('SELECT * FROM category_alerts'),/permission denied/);
 await db.exec('RESET ROLE');
}
await db.exec('DELETE FROM category_reports WHERE user_id<>2');
await db.exec('DELETE FROM users WHERE id=2');
assert.equal((await db.query('SELECT count(*)::int n FROM category_reports')).rows[0].n,0);
console.log('Category SQL passed: access, revisions, idempotence, units, month, snapshots, RLS, cascade.');
await db.close();
