// v47: из первой версии v46 (с training_posts) и из окончательной получается одна и та же схема тренингов.
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {PGlite}=await import(process.env.PGLITE_PATH||'@electric-sql/pglite');
const source=name=>fs.readFile(new URL('../'+name,import.meta.url),'utf8');
// Первая версия v46, которую успели применить 16.11.2026.
const FIRST_V46=`BEGIN;
CREATE TABLE training_chats (
  restaurant_id uuid PRIMARY KEY REFERENCES restaurants(id) ON DELETE CASCADE,
  chat_id bigint NOT NULL UNIQUE CHECK (chat_id < 0),
  sheet_id text CHECK (sheet_id ~ '^[A-Za-z0-9_-]{20,100}$'),
  label text CHECK (char_length(label) BETWEEN 1 AND 40),
  position text NOT NULL DEFAULT 'официант' CHECK (char_length(position) BETWEEN 1 AND 40),
  active boolean NOT NULL DEFAULT true,
  bound_by bigint REFERENCES users(id) ON DELETE SET NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE training_posts (
  restaurant_id uuid NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  session_key text NOT NULL CHECK (char_length(session_key) BETWEEN 1 AND 80),
  chat_id bigint NOT NULL, message_id bigint NOT NULL,
  session jsonb NOT NULL CHECK (jsonb_typeof(session) = 'object' AND octet_length(session::text) <= 2000),
  text_hash text NOT NULL, posted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (restaurant_id, session_key), UNIQUE (chat_id, message_id)
);
CREATE TABLE training_signups (
  restaurant_id uuid NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  session_key text NOT NULL,
  link_id uuid NOT NULL REFERENCES employee_links(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (restaurant_id, session_key, link_id)
);
ALTER TABLE training_chats ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_signups ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON training_chats, training_posts, training_signups FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON training_chats, training_posts, training_signups TO service_role;
COMMIT;`;
const R='aaaaaaaa-0000-0000-0000-000000000001', A='cccccccc-0000-0000-0000-000000000001';
async function schema(v46){
  const db=new PGlite();
  try {
    await db.exec('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS');
    for(const name of ['schema.sql',...Array.from({length:39},(_,i)=>'migration_v'+(i+7)+'.sql')]) await db.exec(await source(name));
    await db.exec(v46);
    await db.exec(await source('migration_v47.sql'));
    await db.exec(await source('migration_v47.sql'));                     // повторный запуск безопасен
    const tables=(await db.query("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'training_%' ORDER BY 1")).rows.map(r=>r.table_name);
    const fks=(await db.query("SELECT count(*)::int AS n FROM pg_constraint WHERE conrelid='training_signups'::regclass AND confrelid='training_sessions'::regclass")).rows[0].n;
    await db.exec('INSERT INTO users(id) VALUES(1),(2)');
    await db.query("INSERT INTO restaurants(id,name,owner_id) VALUES($1,'Тест',1)",[R]);
    await db.query("INSERT INTO employee_links(id,restaurant_id,user_id,report_name,name_key,status) VALUES($1,$2,2,'Аля','аля','approved')",[A,R]);
    await assert.rejects(db.query("INSERT INTO training_signups(restaurant_id,session_key,link_id) VALUES($1,'нет',$2)",[R,A]),/foreign key/);
    await db.exec('SET ROLE anon');
    for(const table of tables) await assert.rejects(db.query('SELECT * FROM '+table),/permission/,table);
    return {tables,fks};
  } finally {await db.close();}
}
const fromFirst=await schema(FIRST_V46);
const fromFinal=await schema(await source('migration_v46.sql'));
assert.deepEqual(fromFirst,fromFinal);
assert.deepEqual(fromFinal.tables,['training_chats','training_invites','training_sessions','training_signups']);
assert.equal(fromFinal.fks,1);
console.log('v47 trainings: the first and the final v46 end in the same schema, rerun is safe, no public access.');
