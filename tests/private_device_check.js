/* Cross-language handoff and migration safety: run with node tests/private_device_check.js. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto,createHmac}=require('node:crypto');
const {execFileSync}=require('node:child_process');

const server={active:false,entries:[
  {id:1,user_id:7,kind:'income',account:'card',signed_amount:500,category:'Чаевые',
   note:'из банка',work_date:'2026-10-04',created_at:'2026-10-04T20:00:00+03:00'},
  {id:2,user_id:7,kind:'expense',account:'cash',signed_amount:-100,category:'Такси',
   note:'трата смены',work_date:'2026-10-04',created_at:'2026-10-04T20:01:00+03:00'}],publicKey:null,
   injectBeforeActivate:false,injectBeforeDeactivate:false,backups:[],
   exitOperation:null,loseExitResponse:false};
const token='test-bot-token';
function device() {
  const local=new Map(),secure=new Map();
  const storage=map=>({getItem:(key,cb)=>cb(null,map.get(key)||null),
                       setItem:(key,value,cb)=>{map.set(key,value);cb(null,true);}});
  return {DeviceStorage:storage(local),SecureStorage:storage(secure),_local:local,_secure:secure};
}
function client(storage,url='https://example.com/app',fastTimeouts=false) {
  const tg={...storage,initData:'signed-test',initDataUnsafe:{user:{id:7}}};
  const location={href:url};
  const emitted=[];
  const window={Telegram:{WebApp:tg},crypto:webcrypto,
    uxEvent:(event,screen)=>emitted.push({event,screen})};
  const context=vm.createContext({window,crypto:webcrypto,location,URL,TextEncoder,TextDecoder,
    AbortController,setTimeout:fastTimeouts?(fn,ms)=>setTimeout(fn,ms===15000||ms===5000?20:ms):setTimeout,clearTimeout,
    btoa:s=>Buffer.from(s,'binary').toString('base64'),
    atob:s=>Buffer.from(s,'base64').toString('binary'),
    history:{replaceState:(_a,_b,next)=>{location.href='https://example.com'+next;}},
    fetch:async(path,options)=>{
      const body=JSON.parse(options.body);
      let status=200,response;
      if(path==='/api/private/prepare') response={active:server.active,entries:server.active?[]:server.entries,
        public_key:server.publicKey,last_exit_operation:server.exitOperation};
      else if(path==='/api/private/activate') {
        if(server.injectBeforeActivate) {
          server.injectBeforeActivate=false;
          server.entries.push({id:3,user_id:7,kind:'expense',account:'cash',signed_amount:-50,
            category:'Кофе',work_date:'2026-10-04',created_at:'2026-10-04T20:02:00+03:00'});
        }
        if(JSON.stringify(body.entry_ids)!==JSON.stringify(server.entries.map(e=>e.id))) {
          status=409;response={error:'Записи изменились во время переноса.'};
        } else {
        assert.equal(storage.DeviceStorage.getItem instanceof Function,true);
        server.publicKey=body.public_key;server.entries=[];server.active=true;response={active:true};
        }
      } else if(path==='/api/private/backups') response={backups:server.backups};
      else if(path==='/api/private/clear_backups') {server.backups=[];response={cleared:true};}
      else if(path==='/api/private/deactivate') {
        if(server.injectBeforeDeactivate) {
          server.injectBeforeDeactivate=false;
          server.backups.push({record_id:'telegram:7:late:0',payload:'sealed-later'});
        }
        if(server.active && server.backups.some(e=>!body.receipts.includes(e.record_id))) {
          status=409;response={error:'Есть записи, которые ещё не перенесены на телефон.'};
        } else if(server.active) {
          server.entries=body.private_entries.map((e,i)=>({...e,id:i+4,user_id:7}));
          server.active=false;server.backups=[];server.exitOperation=body.operation_id;
          response={active:false,count:server.entries.length,operation_id:body.operation_id};
          if(server.loseExitResponse) {server.loseExitResponse=false;throw new Error('network response lost');}
        } else if(server.exitOperation===body.operation_id) response={active:false,count:server.entries.length};
        else {status=409;response={error:'Режим уже выключен.'};}
      }
      else if(path==='/api/private/verify') {
        const sig=createHmac('sha256',token).update('7:'+body.payload).digest('hex');
        response={valid:sig===body.signature};if(!response.valid)status=400;
      } else if(path==='/api/private/rotate') {server.publicKey=body.public_key;response={active:true};}
      else throw new Error(path);
      return {ok:status===200,status,json:async()=>response};
    }});
  vm.runInContext(fs.readFileSync('webapp/private.js','utf8'),context);
  return {pm:window.privateMoney,location,emitted};
}
(async()=>{
  const oldTelegram=device();oldTelegram.isVersionAtLeast=()=>false;
  const oldClient=client(oldTelegram);
  await oldClient.pm.init();
  assert.equal(oldClient.pm.state.lost,false);
  await assert.rejects(oldClient.pm.activate());

  const failing=device();
  failing.DeviceStorage.setItem=(_key,_value,callback)=>callback(null,false);
  const failedClient=client(failing);
  await failedClient.pm.init();
  await assert.rejects(failedClient.pm.activate());
  assert.equal(server.active,false);
  assert.equal(server.entries.length,2);

  const silent=device(),silentClient=client(silent,'https://example.com/app',true);
  await silentClient.pm.init();
  silent.SecureStorage.getItem=()=>{};
  await assert.rejects(silentClient.pm.activate(),/Защищённое хранилище Telegram не отвечает/);
  assert.equal(server.active,false);
  assert.equal(server.entries.length,2);

  const noWrite=device(),noWriteClient=client(noWrite,'https://example.com/app',true);
  await noWriteClient.pm.init();
  noWrite.SecureStorage.setItem=()=>{};
  await assert.rejects(noWriteClient.pm.activate(),/Нет подтверждения записи: защищённое хранилище Telegram/);
  assert.equal(server.active,false);

  const quietWrite=device(),quietClient=client(quietWrite,'https://example.com/app',true);
  await quietClient.pm.init();
  const secureWrite=quietWrite.SecureStorage.setItem;
  quietWrite.SecureStorage.setItem=(key,value)=>secureWrite(key,value,()=>{});
  const localWrite=quietWrite.DeviceStorage.setItem;
  quietWrite.DeviceStorage.setItem=(key,value)=>localWrite(key,value,()=>{});
  assert.equal(await quietClient.pm.activate(),2);
  assert.equal(server.active,true);
  assert.equal(quietClient.pm.entries.length,2);
  server.active=false;server.entries=quietClient.pm.entries.slice();server.publicKey=null;

  const phone=device(),first=client(phone);
  await first.pm.init();
  assert.equal(first.pm.active,false);
  server.injectBeforeActivate=true;
  await assert.rejects(first.pm.activate());
  assert.equal(server.active,false);
  assert.equal(server.entries.length,3);
  assert.equal(first.pm.entries.length,2);
  assert.equal(await first.pm.activate(),3);
  assert.equal(server.active,true);
  assert.deepEqual(server.entries,[]);
  assert.equal(first.pm.entries.length,3);
  assert.equal(first.emitted.length,0); // Migrating old records is not a new save.
  const reopened=client(phone);
  await reopened.pm.init();
  assert.equal(reopened.pm.active,true);
  assert.equal(reopened.pm.entries.length,3);

  const python=`import json,sys,private_payload\nrequest=json.load(sys.stdin)\np,s=private_payload.seal(7,request['key'],{'id':'telegram:7:99:0','kind':'income','account':'card','signed_amount':650,'category':'Чаевые','work_date':'2026-10-04','created_at':'2026-10-04T20:03:00+03:00'},'test-bot-token')\nprint(json.dumps({'payload':p,'signature':s}))`;
  const sealed=JSON.parse(execFileSync(process.env.BUDGET_TEST_PYTHON||'.venv/bin/python',['-c',python],{input:JSON.stringify({key:server.publicKey})}));
  reopened.location.href='https://example.com/app?private_entry='+sealed.payload+'&private_sig='+sealed.signature;
  assert.equal(await reopened.pm.importUrl(),'Запись сохранена на этом устройстве.');
  assert.equal(reopened.pm.entries.length,4);
  assert.deepEqual(reopened.emitted,[{event:'tip_added',screen:'earnings'}]);
  reopened.location.href='https://example.com/app?private_entry='+sealed.payload+'&private_sig='+sealed.signature;
  assert.equal(await reopened.pm.importUrl(),'Эта запись уже есть на устройстве.');
  assert.equal(reopened.pm.entries.length,4);
  assert.equal(reopened.emitted.length,1);
  await reopened.pm.add({id:'local:expense',kind:'expense',account:'cash',signed_amount:-50,
    category:'Такси',work_date:'2026-10-04',created_at:'2026-10-04T20:04:00+03:00'});
  assert.deepEqual(reopened.emitted[1],{event:'expense_added',screen:'earnings'});
  const beforeFailedSave=reopened.emitted.length,write=phone.DeviceStorage.setItem;
  phone.DeviceStorage.setItem=(key,value)=>write(key,value,()=>{});
  const started=Date.now();
  await reopened.pm.edit('local:expense',{signed_amount:-55});
  assert.ok(Date.now()-started<2000,'an exact read should confirm a silent write promptly');
  phone.DeviceStorage.setItem=write;
  phone.DeviceStorage.setItem=(_key,_value,callback)=>callback(null,false);
  await assert.rejects(reopened.pm.add({id:'local:failed',kind:'income',account:'cash',
    signed_amount:1,category:'Чаевые',work_date:'2026-10-04'}));
  phone.DeviceStorage.setItem=write;
  assert.equal(reopened.emitted.length,beforeFailedSave);
  assert.equal(server.entries.length,0);

  const pendingRecord={id:'telegram:7:100:0',kind:'income',account:'card',signed_amount:700,
    category:'Чаевые',work_date:'2026-10-04',created_at:'2026-10-04T20:05:00+03:00'};
  const pending=JSON.parse(execFileSync(process.env.BUDGET_TEST_PYTHON||'.venv/bin/python',['-c',
    `import json,sys,private_payload\nr=json.load(sys.stdin)\np,_=private_payload.seal(7,r['key'],r['record'],'test-bot-token')\nprint(json.dumps({'payload':p}))`],
    {input:JSON.stringify({key:server.publicKey,record:pendingRecord})}));
  const preCancelled={id:'telegram:7:101:0',kind:'income',account:'cash',signed_amount:350,
    category:'Чаевые',work_date:'2026-10-04',created_at:'2026-10-04T20:06:00+03:00'};
  const preCancel={id:preCancelled.id+':cancel',kind:'delete_change',
    target_id:preCancelled.id,signed_amount:0};
  const sealedBeforeImport=JSON.parse(execFileSync(process.env.BUDGET_TEST_PYTHON||'.venv/bin/python',['-c',
    `import json,sys,private_payload\nr=json.load(sys.stdin)\np,_=private_payload.seal(7,r['key'],r['record'],'test-bot-token')\nprint(json.dumps({'payload':p}))`],
    {input:JSON.stringify({key:server.publicKey,record:preCancelled})}));
  const sealedPreCancel=JSON.parse(execFileSync(process.env.BUDGET_TEST_PYTHON||'.venv/bin/python',['-c',
    `import json,sys,private_payload\nr=json.load(sys.stdin)\np,_=private_payload.seal(7,r['key'],r['record'],'test-bot-token')\nprint(json.dumps({'payload':p}))`],
    {input:JSON.stringify({key:server.publicKey,record:preCancel})}));
  server.backups=[{record_id:pendingRecord.id,payload:pending.payload},
    {record_id:preCancelled.id,payload:sealedBeforeImport.payload},
    {record_id:preCancel.id,payload:sealedPreCancel.payload}];
  phone.DeviceStorage.setItem=(_key,_value,callback)=>callback(null,false);
  await assert.rejects(reopened.pm.sync());
  assert.equal(reopened.pm.entries.length,5);
  assert.equal(reopened.emitted.length,beforeFailedSave);
  phone.DeviceStorage.setItem=write;
  assert.equal(await reopened.pm.sync(),3);
  assert.equal(await reopened.pm.sync(),0);
  assert.equal(reopened.pm.entries.length,6);
  assert.equal(reopened.pm.entries.some(e=>e.id===preCancelled.id),false);
  assert.equal(reopened.emitted.length,beforeFailedSave+1);
  await reopened.pm.edit(pendingRecord.id,{signed_amount:800});
  assert.equal(await reopened.pm.sync(),0);
  assert.equal(reopened.pm.entries.find(e=>e.id===pendingRecord.id).signed_amount,800);
  const amendment={id:pendingRecord.id+':account:callback-1',kind:'account_change',
    target_id:pendingRecord.id,account:'cash',signed_amount:0};
  const sealedAmendment=JSON.parse(execFileSync(process.env.BUDGET_TEST_PYTHON||'.venv/bin/python',['-c',
    `import json,sys,private_payload\nr=json.load(sys.stdin)\np,_=private_payload.seal(7,r['key'],r['record'],'test-bot-token')\nprint(json.dumps({'payload':p}))`],
    {input:JSON.stringify({key:server.publicKey,record:amendment})}));
  server.backups.push({record_id:amendment.id,payload:sealedAmendment.payload});
  assert.equal(await reopened.pm.sync(),1);
  assert.equal(reopened.pm.entries.find(e=>e.id===pendingRecord.id).account,'cash');
  assert.equal(reopened.pm.entries.length,6);
  assert.equal(await reopened.pm.sync(),0);
  const cancellation={id:pendingRecord.id+':cancel',kind:'delete_change',
    target_id:pendingRecord.id,signed_amount:0};
  const sealedCancellation=JSON.parse(execFileSync(process.env.BUDGET_TEST_PYTHON||'.venv/bin/python',['-c',
    `import json,sys,private_payload\nr=json.load(sys.stdin)\np,_=private_payload.seal(7,r['key'],r['record'],'test-bot-token')\nprint(json.dumps({'payload':p}))`],
    {input:JSON.stringify({key:server.publicKey,record:cancellation})}));
  server.backups.push({record_id:cancellation.id,payload:sealedCancellation.payload});
  assert.equal(await reopened.pm.sync(),1);
  assert.equal(reopened.pm.entries.some(e=>e.id===pendingRecord.id),false);
  assert.equal(await reopened.pm.sync(),0);

  phone._local.set('money_ledger_v1','corrupted');
  const damaged=client(phone);
  await damaged.pm.init();
  assert.equal(damaged.pm.state.lostReason,'unreadable');
  await assert.rejects(damaged.pm.rotate());
  assert.equal(await damaged.pm.restoreRecent(),0);
  assert.equal(damaged.pm.entries.some(e=>e.id===pendingRecord.id),false);
  assert.equal(phone._local.get('money_ledger_damaged_v1'),'corrupted');
  await damaged.pm.clear();
  assert.equal(server.backups.length,0);
  assert.equal(damaged.pm.entries.length,0);
  await damaged.pm.add({id:'local:exit',kind:'accrual',account:'pending',signed_amount:1200,
    category:'Сервисный сбор',work_date:'2026-10-05',created_at:'2026-10-05T20:00:00+03:00'});
  server.loseExitResponse=true;
  phone.DeviceStorage.setItem=(key,value,callback)=>key==='money_ledger_v1'
    ? callback(null,false) : write(key,value,callback);
  const exited=await damaged.pm.deactivate();
  assert.equal(exited.archivePending,true);
  assert.equal(server.active,false);
  assert.equal(server.entries.length,1);
  assert.equal(server.entries[0].signed_amount,1200);
  const archiveKey='money_ledger_archive_'+server.exitOperation.replace(/-/g,'');
  assert.equal(phone._local.get(archiveKey),phone._local.get('money_ledger_v1'));
  assert.deepEqual(JSON.parse(phone._local.get('money_ledger_archives_v1')),[server.exitOperation]);
  phone.DeviceStorage.setItem=write;
  phone._local.set('money_ledger_v1','corrupted-after-exit');
  const returned=client(phone);
  await returned.pm.init();
  assert.equal(returned.pm.state.exitArchivePending,false);
  assert.equal(returned.pm.state.archiveCount,1);
  assert.ok(phone._local.get(archiveKey));
  assert.equal(returned.pm.entries.length,0);
  assert.equal(await returned.pm.activate(),1); // Re-entering does not duplicate the old local record.
  assert.equal(server.active,true);
  server.injectBeforeDeactivate=true;
  const blockedExit=returned.pm.deactivate();
  await assert.rejects(returned.pm.deactivate());
  await assert.rejects(blockedExit);
  assert.equal(server.active,true);
  assert.equal(returned.pm.entries.length,1);
  server.backups=[];
  const firstArchive=phone._local.get(archiveKey);
  const secondExit=await returned.pm.deactivate();
  assert.equal(secondExit.archivePending,false);
  assert.equal(returned.pm.state.archiveCount,2);
  assert.equal(phone._local.get(archiveKey),firstArchive);
  const afterAgain=client(phone);
  await afterAgain.pm.init();
  assert.equal(afterAgain.pm.state.archiveCount,2);
  assert.equal(await afterAgain.pm.activate(),1);
  const beforeQuota=phone._local.get('money_ledger_v1');
  phone.DeviceStorage.setItem=(key,value,callback)=>key.startsWith('money_ledger_archive_')
    ? callback(null,false) : write(key,value,callback);
  const quotaExit=await afterAgain.pm.deactivate();
  assert.equal(quotaExit.archivePending,true);
  assert.equal(phone._local.get('money_ledger_v1'),beforeQuota);
  phone.DeviceStorage.setItem=write;
  await afterAgain.pm.finishExitArchive();
  assert.equal(afterAgain.pm.state.archiveCount,3);
  assert.equal(await afterAgain.pm.activate(),1);

  const second=client(device());
  await second.pm.init();
  assert.equal(second.pm.state.lost,true);
  assert.equal(second.pm.state.lostReason,'missing');
  assert.equal(second.pm.active,false);
  await assert.rejects(second.pm.deactivate());
  await second.pm.rotate();
  assert.equal(second.pm.active,true);
  assert.equal(second.pm.entries.length,0);
  assert.equal(server.active,true);
  console.log('private device migration, backup recovery, exit, deletion and device loss: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
