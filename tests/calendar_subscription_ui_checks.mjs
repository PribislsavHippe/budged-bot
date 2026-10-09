import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const html=readFileSync(new URL('../webapp/index.html',import.meta.url),'utf8');
const source=html.slice(html.indexOf('let iphoneCalendarData='),html.indexOf("document.getElementById('iphone-calendar-copy').onclick"));
const data={available:true,enabled:true,feed_url:'https://example.test/feed.ics',
  setup_url:'https://example.test/setup',subscribe_url:'https://example.test/setup/subscribe'};
function scenario(platform){
  const elements=new Map(),calls=[],opened=[];
  const document={getElementById(id){if(!elements.has(id))elements.set(id,{});return elements.get(id);}};
  const context={document,navigator:{userAgent:'',maxTouchPoints:0},AbortSignal,
    tg:{platform,initData:'signed',openLink(url){opened.push(url);}},
    window:{location:{assign(url){opened.push(url);}}},
    fetch:async (url,options)=>{calls.push(JSON.parse(options.body).action);return {ok:true,json:async()=>data};}};
  runInNewContext(source,context);
  return {context,document,calls,opened,button:document.getElementById('iphone-calendar-toggle')};
}
const ios=scenario('ios');
await ios.context.iphoneCalendarRefresh();
assert.equal(ios.opened.length,0,'preparing a URL does not open or install a calendar');
assert.equal(ios.button.textContent,'Подключить iPhone ›');
const firstClick=ios.button.onclick();
assert.deepEqual(ios.opened,[data.subscribe_url],'handoff happens synchronously within the tap');
await firstClick;
assert.deepEqual(ios.calls,['create']);
assert.deepEqual(ios.opened,[data.subscribe_url],'first tap starts subscription without instruction');
assert.equal(ios.button.disabled,false);
await ios.button.onclick();
assert.deepEqual(ios.calls,['create'],'reopening retains the subscription URL');
assert.equal(ios.opened.length,2);

const android=scenario('android');
await android.context.iphoneCalendarRefresh();
await android.button.onclick();
assert.deepEqual(android.opened,[data.setup_url],'Android retains its supported setup path');

const failure=scenario('ios');
failure.context.fetch=async()=>{throw new Error('Не получилось подключить календарь. Попробуй ещё раз.');};
await failure.button.onclick();
assert.equal(failure.opened.length,0);
assert.equal(failure.button.disabled,false);
assert.match(failure.document.getElementById('iphone-calendar-status').textContent,/Попробуй ещё раз/);
failure.context.fetch=ios.context.fetch;
await failure.context.iphoneCalendarRefresh();
await failure.button.onclick();
assert.deepEqual(failure.opened,[data.subscribe_url],'failed creation remains retryable');
console.log('Calendar subscription UI checks passed.');
