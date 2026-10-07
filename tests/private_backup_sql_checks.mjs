/* Run with PGLITE_PATH=/path/to/@electric-sql/pglite/dist/index.js node tests/private_backup_sql_checks.mjs */
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  await db.exec(await source('schema.sql'));
  await db.exec(await source('migration_v15.sql'));
  await db.exec(await source('migration_v20.sql'));
  await db.exec("INSERT INTO users(id) VALUES(7); INSERT INTO entries(user_id,kind,account,signed_amount,category) VALUES(7,'income','card',500,'Чаевые')");
  const key={kty:'RSA',alg:'RSA-OAEP-256',n:'public-modulus',e:'AQAB'};
  await assert.rejects(db.query('SELECT activate_private_money_with_backups($1,$2,$3,$4)',
    [7,[1],key,[]]),/private_entries_changed/);
  assert.equal((await db.query('SELECT count(*)::int n FROM entries WHERE user_id=7')).rows[0].n,1);
  await db.query('SELECT activate_private_money_with_backups($1,$2,$3,$4)',
    [7,[1],key,[{record_id:'1',payload:'ciphertext'}]]);
  assert.equal((await db.query('SELECT count(*)::int n FROM entries WHERE user_id=7')).rows[0].n,0);
  assert.equal((await db.query('SELECT private_money_mode FROM users WHERE id=7')).rows[0].private_money_mode,true);
  assert.equal((await db.query('SELECT count(*)::int n FROM private_money_backups WHERE user_id=7')).rows[0].n,1);
  await assert.rejects(db.query('SELECT activate_private_money_with_backups($1,$2,$3,$4)',
    [7,[],key,[]]),/private_already_active/);
  await assert.rejects(db.query('SELECT store_private_money_backup($1,$2,$3,$4)',
    [7,'telegram:7:2:0','ciphertext','wrong-key']),/private_key_changed/);
  const keyId=(await db.query("SELECT md5('public-modulus') AS id")).rows[0].id;
  await db.query('SELECT store_private_money_backup($1,$2,$3,$4)',
    [7,'telegram:7:2:0','ciphertext',keyId]);
  await db.query('SELECT store_private_money_backup($1,$2,$3,$4)',
    [7,'telegram:7:2:0','ciphertext',keyId]);
  assert.equal((await db.query("SELECT count(*)::int n FROM private_money_backups WHERE user_id=7 AND record_id='telegram:7:2:0'")).rows[0].n,1);
  await db.exec('SET ROLE anon');
  await assert.rejects(db.query('SELECT * FROM private_money_backups'),/permission denied/);
  await db.exec('RESET ROLE');
  await db.exec('DELETE FROM users WHERE id=7');
  assert.equal((await db.query('SELECT count(*)::int n FROM private_money_backups')).rows[0].n,0);
  console.log('Private backup SQL: atomic activation, idempotency, key check, access and cascade OK');
} finally {await db.close();}
