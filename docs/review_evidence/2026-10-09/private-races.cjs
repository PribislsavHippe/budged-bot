const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {webcrypto:crypto}=require('node:crypto');
const source=fs.readFileSync('webapp/private.js','utf8');
const clone=x=>JSON.parse(JSON.stringify(x));
const encode=b=>Buffer.from(b).toString('base64url');
const ledgerName='money_ledger_v1';
const record=(id,amount)=>({id,user_id:7,kind:'income',account:'cash',signed_amount:amount,
  category:'Чаевые',work_date:'2026-10-09',created_at:'2026-10-09T12:00:00+03:00'});
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
        if(server.changeBeforeActivation)server.entries[0].signed_amount=900;
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
  const pm=client(phone,server);await pm.init();await pm.activate();
  assert.equal(pm.entries[0].signed_amount,500);
  const imported=await pm.sync();
  assert.equal(imported,0);
  const restored=client(phone,server);await restored.init();
  assert.equal(restored.entries[0].signed_amount,500);
  console.log('CONFIRMED activation: server edit 500 -> 900; persisted phone=500; sync imported=0; plaintext server entries=0');

  // Pause the first ledger write and finish the second one first.
  const original=phone.DeviceStorage.setItem;let releaseFirst,markStarted;
  const started=new Promise(resolve=>markStarted=resolve);
  let count=0;
  phone.DeviceStorage.setItem=(key,value,cb)=>{
    if(key===ledgerName&&++count===1){releaseFirst=()=>original(key,value,cb);markStarted();}
    else original(key,value,cb);
  };
  const first=pm.add(record('local:first',100));await started;
  const second=pm.add(record('local:second',200));await second;
  releaseFirst();await first;
  const after=client(phone,server);await after.init();
  assert.equal(pm.entries.length,3);
  assert.equal(after.entries.length,2);
  assert.equal(after.entries.some(e=>e.id==='local:second'),false);
  console.log('CONFIRMED concurrent local saves: both add() returned success; memory records=3; reopened records=2; second confirmed record missing');
})().catch(e=>{console.error(e);process.exitCode=1});
