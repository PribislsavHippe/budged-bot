import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_MODULE||'@electric-sql/pglite');
const db=new PGlite();
await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;');
for(const file of ['schema.sql','migration_v8.sql','migration_v9.sql','migration_v10.sql',
                   'migration_v11.sql','migration_v12.sql','migration_v22.sql'])
  await db.exec(await readFile(new URL('../'+file,import.meta.url),'utf8'));
await db.exec('INSERT INTO users(id) VALUES(1),(2),(3),(4),(5)');
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const action=async(actor,kind,args)=>(await db.query('SELECT identity_action($1,$2,$3::jsonb) AS row',
  [actor,kind,JSON.stringify(args)])).rows[0].row;
const transfer=async(actor,member,source,target)=>db.query('SELECT identity_transfer($1,$2,$3,$4)',
  [actor,member,source,target]);
await action(1,'create',{id:id(1),name:'Первый'});
await action(1,'create',{id:id(2),name:'Второй'});
await action(2,'create',{id:id(3),name:'Чужой'});
await action(1,'invite',{restaurant_id:id(1),hash:'source'});
await action(1,'invite',{restaurant_id:id(2),hash:'destination'});
await action(3,'request',{id:id(10),hash:'source',name:'Аня',name_key:'аня'});
await action(1,'approve',{id:id(10),restaurant_id:id(1)});
await db.exec(`UPDATE employee_links SET requested_at='2026-09-01' WHERE id='${id(10)}'`);
await assert.rejects(transfer(2,id(10),id(1),id(3)),/identity_forbidden/);
await assert.rejects(transfer(1,id(10),id(1),id(3)),/identity_forbidden/);
await assert.rejects(transfer(1,id(10),id(2),id(1)),/identity_stale/);
await db.exec('SET ROLE anon');
await assert.rejects(transfer(1,id(10),id(1),id(2)),/permission denied/);
await db.exec('RESET ROLE');
await transfer(1,id(10),id(1),id(2));
const moved=(await db.query('SELECT * FROM employee_links WHERE id=$1',[id(10)])).rows[0];
assert.equal(moved.restaurant_id,id(2));
assert.equal(moved.status,'approved');
assert.ok(new Date(moved.requested_at)>new Date('2026-09-01'));
assert.equal(moved.reviewed_by,1);
await assert.rejects(transfer(1,id(10),id(1),id(2)),/identity_stale/);
await action(4,'request',{id:id(11),hash:'source',name:'Боря',name_key:'боря'});
await action(1,'approve',{id:id(11),restaurant_id:id(1)});
await action(5,'request',{id:id(12),hash:'destination',name:'Боря',name_key:'боря'});
await action(1,'approve',{id:id(12),restaurant_id:id(2)});
await assert.rejects(transfer(1,id(11),id(1),id(2)),/duplicate key/);
assert.equal((await db.query('SELECT restaurant_id FROM employee_links WHERE id=$1',[id(11)])).rows[0].restaurant_id,id(1));
await db.close();
console.log('Restaurant transfer SQL checks passed: ownership, role, stale state, name collision and membership date.');
