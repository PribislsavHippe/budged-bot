import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const call=async(actor,action,args={})=>(await db.query('SELECT identity_action($1,$2,$3::jsonb) r',[actor,action,JSON.stringify(args)])).rows[0].r;
const request=(uid,hash)=>call(uid,'request',{id:id(uid+20),hash,name:`Person ${uid}`,name_key:`person ${uid}`});
try {
 await db.exec('CREATE ROLE anon;CREATE ROLE authenticated;CREATE ROLE service_role BYPASSRLS');
 for(const name of ['schema.sql',...Array.from({length:22},(_,i)=>`migration_v${i+7}.sql`)])await db.exec(await source(name));
 await db.exec('INSERT INTO users(id) SELECT generate_series(1,10)');
 await call(1,'create',{id:id(1),name:'First'});
 await call(1,'create',{id:id(2),name:'Second'});
 await call(2,'create',{id:id(3),name:'Other owner'});
 await call(1,'invite',{restaurant_id:id(1),hash:'expired-stored'});
 await call(1,'invite',{restaurant_id:id(2),hash:'overwritten'});
 await call(1,'invite',{restaurant_id:id(2),hash:'current'});
 await db.exec("UPDATE restaurants SET invite_expires_at=now()-interval '30 days'");
 await assert.rejects(request(3,'expired-stored'),/identity_invite_expired/);
 const migration=await source('migration_v29.sql');
 await db.exec(migration);await db.exec(migration); // Safe to rerun.
 await db.exec('SET ROLE service_role');
 const restored=await request(3,'expired-stored');
 assert.equal(restored.restaurant_id,id(1));assert.equal(restored.status,'pending');
 assert.equal((await request(3,'expired-stored')).id,restored.id);
 await assert.rejects(request(4,'overwritten'),/identity_invite_expired/);
 await assert.rejects(request(4,'unknown'),/identity_invite_expired/);
 await assert.rejects(call(2,'approve',{id:restored.id}),/identity_forbidden/);
 await call(1,'approve',{id:restored.id});
 await assert.rejects(request(3,'current'),/identity_other_restaurant/);
 await assert.rejects(call(2,'invite',{restaurant_id:id(1),hash:'attack'}),/identity_forbidden/);
 await assert.rejects(call(1,'invite',{hash:'ambiguous'}),/identity_select_restaurant/);
 await call(1,'invite',{restaurant_id:id(1),hash:'new-permanent'});
 assert.equal((await db.query('SELECT invite_expires_at FROM restaurants WHERE id=$1',[id(1)])).rows[0].invite_expires_at,null);
 await assert.rejects(request(5,'expired-stored'),/identity_invite_expired/);
 assert.equal((await request(5,'new-permanent')).status,'pending');
 // Calendar expiry cannot invalidate a matching code anymore.
 await db.exec("UPDATE restaurants SET invite_expires_at=now()-interval '100 years' WHERE invite_hash='new-permanent'");
 assert.equal((await request(6,'new-permanent')).restaurant_id,id(1));
 await db.exec('RESET ROLE');
 for(const role of ['anon','authenticated']){
  await db.exec('SET ROLE '+role);
  await assert.rejects(request(7,'new-permanent'),/permission denied/);
  await assert.rejects(db.query('SELECT * FROM restaurants'),/permission denied/);
  await db.exec('RESET ROLE');
 }
 await db.exec('DELETE FROM users WHERE id=1');
 await db.exec('SET ROLE service_role');
 await assert.rejects(request(8,'new-permanent'),/identity_invite_expired/);
 console.log('Permanent invitations passed: expired codes revived, pending approval, rotation, ownership, roles, orphan rejection, rerun and fresh migration chain.');
} finally {await db.close();}
