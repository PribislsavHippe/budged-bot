// Корзина в таблице графика (правило владельца 16.11.2026): убранный сотрудник серый на своей строке,
// его клетки не правятся и не считаются в «В смене»; экранирование имён сохраняется.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const window={};
runInNewContext(readFileSync(new URL('../webapp/schedule-grid.js',import.meta.url),'utf8'),{window,Date,Math,String,Number,Object,Array,Set,Map,JSON,isNaN,parseFloat,parseInt});
const G=window.ScheduleGrid;
const days=['2026-11-16','2026-11-17'];
const html=G.table({days,edit:true,weekGap:false,people:[{name:'Аня',cells:{'2026-11-16':'9'}},{name:'Боря',cells:{}}],
  removed:[{name:'<Ира>',cells:{'2026-11-16':'10','2026-11-17':'10'},index:1}]});
const rows=[...html.matchAll(/<tr class="([^"]*)"><th class="sg-name" scope="row" ([^>]*)>([^<]*)/g)].map(m=>[m[1],m[2],m[3]]);
assert.deepEqual(rows.map(r=>r[2]),['Аня','&lt;Ира&gt;','Боря'],'removed person stays on their own row, escaped');
assert.equal(rows[1][0],'sg-gone');
assert.match(rows[1][1],/data-gone="0"/);
const gone=html.slice(html.indexOf('class="sg-gone"'),html.indexOf('</tr>',html.indexOf('class="sg-gone"')));
assert.ok(!gone.includes('data-r='),'removed cells are not editable targets');
assert.ok(!gone.includes('sg-x'),'no second removal button');
assert.ok(!gone.includes('draggable'),'removed shifts cannot be dragged');
const footer=html.slice(html.indexOf('<tfoot>'));
assert.deepEqual([...footer.matchAll(/<b>(\d+)<\/b>/g)].map(m=>+m[1]),[1,0],'removed shifts are not counted');
// Решения владельца 16.11.2026: желательные выходные видны, у каждого счётчик смен.
const marked=G.table({days,edit:true,counts:true,weekGap:false,people:[
  {name:'Аня',cells:{'2026-11-16':'9'},wish:['2026-11-16','2026-11-17']},
  {name:'Боря',cells:{},off:['2026-11-16'],wish:['2026-11-16']}]});
assert.match(marked,/class="sg-s  wished" title="Желательный выходной"><b>9<\/b>/,'a shift on a wished day is outlined');
assert.match(marked,/<span class="sg-s wish" title="Желательный выходной">жел<\/span>/,'an empty wished day is marked');
assert.ok(!/sg-s off">вых<\/span>[^<]*<span class="sg-s wish/.test(marked),'mandatory day off wins over a wish');
assert.deepEqual([...marked.matchAll(/<small class="sg-cnt"[^>]*>([^<]+)<\/small>/g)].map(m=>m[1]),['смен','1','0'],'shift counts per person');
assert.ok(!G.table({days,people:[{name:'Аня',cells:{}}]}).includes('sg-cnt'),'team view has no counter');
console.log('Roster grid UI checks passed: greyed row in place, not editable, not counted, escaped; wished days marked; shift counts.');
