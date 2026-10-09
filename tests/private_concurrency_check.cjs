const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {webcrypto:crypto}=require('node:crypto');
const source=fs.readFileSync('webapp/private.js','utf8');
const clone=x=>JSON.parse(JSON.stringify(x));
const encode=b=>Buffer.from(b).toString('base64url');
const ledgerName='money_ledger_v1';
const record=(id,amount)=>({id,user_id:7,kind:'income',account:'cash',signed_amount:amount,
  category:'Чаевые',work_date:'2026-10-09',created_at:'2026-10-09T12:00:00+03:00',revision:1});
function device(){
  const local=new Map(),secure=new Map();
  const storage=map=>({getItem:(key,cb)=>cb(null,map.get(key)||null),
    setItem:(key,value,cb)=>{map.set(key,value);cb(null,true);}});
  return {DeviceStorage:storage(local),SecureStorage:storage(secure),local,secure};
}
async function seal(publicJwk,entry){
  const bytes=crypto.getRandomValues(new Uint8Array(32)),iv=crypto.getRandomValues(new Uint8Array(12));
  const rsa=await crypto.subtle.importKey('jwk',publicJwk,{name:'RSA-OAEP',hash:'SHA-256'},false,['encrypt']);
  const key=await crypto.subtle.importKey('raw',bytes,{name:'AES-GCM'},false,['encrypt']);
  const wrapped=new Uint8Array(await crypto.subtle.encrypt({name:'RSA-OAEP'},rsa,bytes));
  const cipher=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv,
    additionalData:new TextEncoder().encode('7')},key,new TextEncoder().encode(JSON.stringify(entry))));
  return encode(Buffer.concat([Buffer.from([1]),Buffer.from(wrapped),Buffer.from(iv),Buffer.from(cipher)]));
}
function client(storage,server){
  const window={crypto,Telegram:{WebApp:{...storage,initData:'test',initDataUnsafe:{user:{id:7}}}}};
  const context=vm.createContext({window,crypto,TextEncoder,TextDecoder,URL,
    location:{href:'https://example.com/app'},history:{replaceState:()=>{}},
    AbortController,setTimeout,clearTimeout,btoa:s=>Buffer.from(s,'binary').toString('base64'),
    atob:s=>Buffer.from(s,'base64').toString('binary'),fetch:async(path,options)=>{
      const body=JSON.parse(options.body);let result;
      if(path==='/api/private/prepare')result={active:server.active,
        entries:body.state_only||server.active?[]:clone(server.entries),public_key:server.key};
      else if(path==='/api/private/activate'){
        if(server.changeBeforeActivation){server.entries[0].signed_amount=900;server.entries[0].revision++;server.changeBeforeActivation=false;}
        if(body.entry_versions.some(v=>server.entries.find(e=>e.id===v.id)?.revision!==v.revision))
          return {ok:false,status:409,json:async()=>({error:'Записи изменились во время переноса.'})};
        assert.deepEqual(body.entry_ids,server.entries.map(e=>e.id));
        server.key=body.public_key;
        server.backups=await Promise.all(server.entries.map(async e=>({record_id:String(e.id),payload:await seal(server.key,e)})));
        server.entries=[];server.active=true;result={active:true};
      }else if(path==='/api/private/backups')result={backups:server.backups};
      else throw Error(path);
      return {ok:true,status:200,json:async()=>clone(result)};
    }});
  vm.runInContext(source,context);
  return window.privateMoney;
}
(async()=>{
  const phone=device(),server={active:false,entries:[record(1,500)],key:null,backups:[],changeBeforeActivation:true};
  const pm=client(phone,server);await pm.init();
  await assert.rejects(pm.activate(),/Записи изменились/);
  assert.equal(server.active,false);assert.equal(server.entries[0].signed_amount,900);
  await pm.activate();assert.equal(pm.entries[0].signed_amount,900);
  assert.equal(server.entries.length,0);
  console.log('version mismatch aborts transfer; retry preserves the corrected amount: OK');

  const otherPhone=device(),otherServer={active:false,entries:[record(10,300)],key:null,backups:[],changeBeforeActivation:true};
  const other=client(otherPhone,otherServer);await other.init();
  await assert.rejects(other.activate(),/Записи изменились/);
  otherServer.entries=[]; // The employee deleted the stale staged entry.
  await other.activate();assert.equal(other.entries.length,0);
  console.log('retry does not resurrect a deleted staged entry: OK');

  // Pause the first ledger write and finish the second one first.
  const original=phone.DeviceStorage.setItem;let releaseFirst,markStarted;
  const started=new Promise(resolve=>markStarted=resolve);
  let count=0;
  phone.DeviceStorage.setItem=(key,value,cb)=>{
    if(key===ledgerName&&++count===1){releaseFirst=()=>original(key,value,cb);markStarted();}
    else original(key,value,cb);
  };
  const first=pm.add(record('local:first',100));await started;
  const second=pm.add(record('local:second',200));
  await new Promise(resolve=>setTimeout(resolve,10));assert.equal(count,1);
  releaseFirst();await Promise.all([first,second]);
  const after=client(phone,server);await after.init();
  assert.equal(pm.entries.length,3);
  assert.equal(after.entries.length,3);
  assert.equal(after.entries.some(e=>e.id==='local:second'),true);
  console.log('concurrent local saves survive reopening: OK');
  const cancelled=record('local:cancelled',300);
  cancelled.source_key='calendar:00000000-0000-4000-8000-000000000030';
  await after.add(cancelled);await after.remove(cancelled.id);
  assert.equal(await after.add(cancelled),false);
  assert.equal(after.state.receipts.includes(cancelled.source_key),true);
  console.log('a cancelled device operation cannot be re-added by a retry: OK');
  assert.deepEqual(clone(pm.payload),{});
})().catch(e=>{console.error(e);process.exitCode=1});
