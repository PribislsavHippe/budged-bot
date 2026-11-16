// Календарь выходных как виджет Telegram: отрисовка, нажатия и что уходит боту (заглушки страницы и Telegram).
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {runInNewContext} from 'node:vm';

const python=process.env.BUDGET_TEST_PYTHON||'.venv/bin/python';
const html=execFileSync(python,['-c','import days_off_widget,sys;sys.stdout.write(days_off_widget.HTML)'],{encoding:'utf8'});
const script=html.slice(html.indexOf('<script>')+8,html.lastIndexOf('</script>'));
const HEX='dddddddd000000000000000000000001',ID=HEX.slice(0,8);
function widget(chatData,withTelegram=true){
  const els={},requests=[],events={};
  const document={getElementById:id=>els[id]??=({id,textContent:'',innerHTML:''})};
  const W=withTelegram?{chatData,request:q=>{requests.push(q);return {then:(ok,fail)=>{W.last={ok,fail}}}},
    onEvent:(name,fn)=>{events[name]=fn},ready(){}}:undefined;
  const context={window:{TelegramWidget:W},document,JSON,Date,Math,parseInt};
  runInNewContext(script,context);
  return {context,els,requests,events,W};
}
const saved=JSON.stringify({id:HEX,s:'2026-11-16',e:'2026-11-30',f:'2026-11-18',r:'c',p:'e8'});
const w=widget(saved);
assert.equal(w.els.t.textContent,'График на 16–30 ноября');
const grid=w.els.g.innerHTML;
assert.equal((grid.match(/class="w"/g)||[]).length,7,'weekday header');
assert.equal((grid.match(/<button/g)||[]).length,15,'one button per day of the period');
assert.match(grid,/<button class="d  x" disabled>16<i><\/i><\/button>/,'past days are disabled');
assert.match(grid,/<button class="d r" onclick="tap\(2\)">18<i>вых<\/i>/,'required day');
assert.match(grid,/<button class="d p" onclick="tap\(9\)">25<i>жел<\/i>/,'preferred day');
assert.equal(w.els.n.textContent,'Обязательных: 2 · Желательных: 1 · С часами: 0');
// Нажатие: пусто → вых → жел → часы 9–17 → пусто.
w.context.tap(4);assert.match(w.els.g.innerHTML,/class="d r" onclick="tap\(4\)">20<i>вых/);
w.context.tap(4);assert.match(w.els.g.innerHTML,/class="d p" onclick="tap\(4\)">20<i>жел/);
w.context.tap(4);assert.match(w.els.g.innerHTML,/class="d h k" onclick="tap\(4\)">20<i>9–17/);
assert.match(w.els.e.innerHTML,/^<span>20 ноября<\/span>.*<b>9:00<\/b>.*–.*<b>17:00<\/b>/);
w.context.tap(4);assert.match(w.els.g.innerHTML,/class="d " onclick="tap\(4\)">20<i><\/i>/);
assert.equal(w.els.e.innerHTML,'','no hours editor without a selected day');
w.context.tap(4);
// Часы: «−»/«+» по часу, конец не раньше начала; следующий день берёт последние часы.
w.context.tap(6);w.context.tap(6);w.context.tap(6);              // 22-е
w.context.mv(1,2);w.context.mv(0,-2);                            // 8–18
assert.match(w.els.g.innerHTML,/22<i>8–18/);
for(let k=0;k<20;k++)w.context.mv(0,2);                          // начало упирается в конец
assert.match(w.els.e.innerHTML,/<b>17:00<\/b>.*<b>18:00<\/b>/);
w.context.mv(0,-18);                                             // снова 8:00
w.context.tap(7);w.context.tap(7);w.context.tap(7);              // 23-е — те же 8–18
assert.match(w.els.g.innerHTML,/23<i>8–18/);
w.context.tap(7);                                                // выбрано — убрать
w.context.tap(6);assert.match(w.els.g.innerHTML,/class="d h k" onclick="tap\(6\)">22/,'tap selects an hours day');
assert.equal(w.els.n.textContent,'Обязательных: 3 · Желательных: 1 · С часами: 1');
// «Сохранить» — весь выбор одним запросом не длиннее 64 байт.
w.els.sv.onclick();
assert.equal(w.requests.at(-1),`o:${ID}:${(0b1100|1<<4).toString(36)}:e8:64o`);
assert.ok(Buffer.byteLength(w.requests.at(-1))<=64);
assert.equal(w.els.m.textContent,'Сохраняю…');
w.W.last.ok({result:'ok'});assert.equal(w.els.m.textContent,'Записал.');
w.els.sv.onclick();w.W.last.ok({result:'Больше 12 выходных за один график записать нельзя.'});
assert.equal(w.els.m.textContent,'Больше 12 выходных за один график записать нельзя.');
w.els.sv.onclick();w.W.last.fail({code:'RATE_LIMITED'});assert.match(w.els.m.textContent,/Подожди/);
// Бот обновил данные — виджет показывает сохранённое.
w.W.chatData=JSON.stringify({id:HEX,s:'2026-11-16',e:'2026-11-30',f:'2026-11-18',r:'0',p:'0',h:'46m',ok:'18.11 в 14:05'});
w.events.chatDataChanged();
assert.equal(w.els.n.textContent,'Обязательных: 0 · Желательных: 0 · С часами: 1');
assert.match(w.els.g.innerHTML,/class="d h" onclick="tap\(4\)">20<i>9–17/);
assert.equal(w.els.m.textContent,'Записал 18.11 в 14:05.');
// «Пожеланий нет».
w.els.no.onclick();assert.equal(w.requests.at(-1),`n:${ID}`);
assert.equal(w.els.n.textContent,'Обязательных: 0 · Желательных: 0 · С часами: 0');
// В браузере без Telegram виджет не падает.
const plain=widget(saved,false);assert.equal(plain.els.t.textContent,'График');
assert.equal(widget('{}').els.t.textContent,'График');
console.log('Days-off widget checks passed: grid, past days, cycle of marks, hours, one save request, answers, no wishes, refresh.');
