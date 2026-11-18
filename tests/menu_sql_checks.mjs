// v48: подсказки по меню — новая загрузка того же файла заменяет старую, старую можно вернуть неделю, доступ только у сервера.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const db=new PGlite();
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
const A='aaaaaaaa-0000-4000-8000-000000000001', B='aaaaaaaa-0000-4000-8000-000000000002';
const C='aaaaaaaa-0000-4000-8000-000000000003', P='bbbbbbbb-0000-4000-8000-000000000001';
const active=async()=>(await db.query(
  "SELECT id FROM menu_imports WHERE activated_at IS NOT NULL AND replaced_at IS NULL ORDER BY id")).rows.map(r=>r.id);
try {
  await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
  for(const name of ['schema.sql',...Array.from({length:42},(_,i)=>'migration_v'+(i+7)+'.sql')]) await db.exec(await source(name));
  await db.exec('SET ROLE service_role');
  await db.query("INSERT INTO menu_imports(id,source,file_key,created_by) VALUES($1,'Кухня','f1',1)",[A]);
  await db.query("INSERT INTO menu_photos(id,import_id,data) VALUES($1,$2,'AAAA')",[P,A]);
  await db.query(`INSERT INTO menu_items(id,import_id,position,card,photo_id) VALUES(gen_random_uuid(),$1,0,'{"name":"Суп"}',$2)`,[A,P]);
  assert.deepEqual(await active(),[],'загрузка не видна, пока не включена');
  assert.equal((await db.query('SELECT activate_menu_import($1) AS r',[A])).rows[0].r,'ok');
  assert.equal((await db.query('SELECT activate_menu_import($1) AS r',[A])).rows[0].r,'active');
  await assert.rejects(db.query("INSERT INTO menu_imports(id,source,file_key,created_by) VALUES($1,'Другое','f1',1)",[C]),/unique/);
  await db.query("INSERT INTO menu_imports(id,source,file_key,created_by) VALUES($1,'КУХНЯ','f2',1)",[B]);
  assert.equal((await db.query('SELECT activate_menu_import($1) AS r',[B])).rows[0].r,'ok');
  assert.deepEqual(await active(),[B],'новая версия того же файла заменила старую');
  assert.ok((await db.query('SELECT replaced_at FROM menu_imports WHERE id=$1',[A])).rows[0].replaced_at,'старая сохранена');
  assert.equal((await db.query('SELECT activate_menu_import($1) AS r',[A])).rows[0].r,'ok');
  assert.deepEqual(await active(),[A],'старую можно вернуть');
  await db.query("UPDATE menu_imports SET replaced_at=now()-interval '8 days' WHERE id=$1",[B]);
  await db.query("INSERT INTO menu_imports(id,source,file_key,created_by,created_at) VALUES($1,'Бар','f3',1,now()-interval '2 days')",[C]);
  assert.equal((await db.query('SELECT activate_menu_import($1) AS r',[A])).rows[0].r,'active');
  await db.query("INSERT INTO menu_imports(id,source,file_key,created_by) VALUES(gen_random_uuid(),'Завтраки','f4',1)");
  const other=(await db.query("SELECT id FROM menu_imports WHERE file_key='f4'")).rows[0].id;
  await db.query('SELECT activate_menu_import($1)',[other]);
  assert.deepEqual((await db.query('SELECT file_key FROM menu_imports ORDER BY file_key')).rows.map(r=>r.file_key),['f1','f4'],
                   'через неделю замена удалена, брошенная загрузка — через сутки');
  assert.equal((await db.query('SELECT activate_menu_import($1) AS r',[B])).rows[0].r,'missing');
  await assert.rejects(db.query(`INSERT INTO menu_items(id,import_id,position,card) VALUES(gen_random_uuid(),$1,1,'[]')`,[A]),/check/);
  await db.exec('RESET ROLE; SET ROLE anon');
  for(const table of ['menu_imports','menu_photos','menu_items'])
    await assert.rejects(db.query('SELECT * FROM '+table),/permission/);
  await assert.rejects(db.query('SELECT activate_menu_import($1)',[A]),/permission/);
  await db.exec('RESET ROLE');
  await db.query('DELETE FROM menu_imports WHERE id=$1',[A]);
  for(const table of ['menu_photos','menu_items'])
    assert.equal((await db.query('SELECT count(*)::int AS n FROM '+table)).rows[0].n,0,table);
  console.log('v48 menu: staged imports, one active per file name, replaced kept a week and can be restored, no public access, cascade.');
} finally {await db.close();}
