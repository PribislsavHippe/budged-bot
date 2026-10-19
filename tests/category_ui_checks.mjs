import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{value:'',hidden:false,open:false,innerHTML:'',textContent:'',addEventListener(){},setAttribute(){},reset(){},scrollIntoView(){}});return elements.get(id);}
const metric={total:null,official:null,recorded:0,estimated:0,target:null,pct:null,remaining:null,cutoff:null};
const data={month:'2026-10',glass_price:850,targets:{},metrics:Object.fromEntries(['wine','cocktails','desserts','turnover','postcards','dvd'].map(k=>[k,{...metric}])),events:[],reports:[],has_settings:false,
 category_metrics:[{...metric,id:'11111111-1111-4111-8111-111111111111',name:'Гарниры',unit:'amount',total:1500,official:1500,cutoff:'2026-10-14'},
 {...metric,id:'33333333-3333-4333-8333-333333333333',name:'Новая категория',unit:'number',total:100,official:100,cutoff:'2026-10-14'},
 {...metric,id:'22222222-2222-4222-8222-222222222222',name:'<img src=x onerror="x">',unit:'count',total:3,official:3,cutoff:'2026-10-14'}]};
const context={document:{getElementById:element,querySelectorAll(){return [];}},window:{addEventListener(){},dispatchEvent(){}},sessionStorage:{getItem(){return null;},removeItem(){},setItem(){}},fetch:async()=>({ok:true,json:async()=>data}),AbortSignal,Date,Number,Set,JSON,Object,String,Math,Event};
vm.runInNewContext(fs.readFileSync(new URL('../webapp/sales.js',import.meta.url),'utf8'),context);
await element('sales-reload').onclick();
const rendered=element('sales-metrics').innerHTML;
assert.ok(rendered.includes('Гарниры'));assert.ok(rendered.includes('1 500')||rendered.includes('1 500'));
assert.ok(rendered.includes('3 шт.'));assert.ok(element('sales-freshness').textContent.includes('14'));
assert.ok(!rendered.match(/Новая категория[\s\S]*?100 ₽/));assert.ok(rendered.includes('&lt;img'));assert.ok(!rendered.includes('<img'));
assert.ok(!rendered.includes('>Вино<'));assert.equal(element('sales-turnover').hidden,true);
console.log('Category UI passed: actual renderer, dynamic names/units, XSS escaping, no blank legacy cards.');
