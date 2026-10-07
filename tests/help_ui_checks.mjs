// The help entry must respond inside the miniapp before any Telegram navigation.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const listeners={};
const opened=[];
const elements=new Map();
function element(id){
  if(!elements.has(id)){
    const classes=new Set();
    elements.set(id,{
      id,hidden:false,textContent:'',href:'',
      classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
      focus(){document.activeElement=this;},
      addEventListener(name,callback){listeners[`${id}:${name}`]=callback;},
    });
  }
  return elements.get(id);
}
const document={
  activeElement:element('ux-help'),hidden:false,
  getElementById:element,
  querySelector:()=>null,
  addEventListener(name,callback){listeners[`document:${name}`]=callback;},
};
const window={
  Telegram:{WebApp:{initData:'signed',openTelegramLink:url=>opened.push(url)}},
  addEventListener(name,callback){listeners[`window:${name}`]=callback;},
  open:url=>opened.push(url),
};
const context={window,document,AbortSignal,fetch:async()=>({ok:true,json:async()=>({enabled:false,available:false})})};
vm.runInNewContext(readFileSync(new URL('../webapp/ux.js',import.meta.url),'utf8'),context);
listeners['window:DOMContentLoaded']();
element('ux-help').onclick();
assert.ok(element('help-panel').classList.contains('on'));
assert.equal(element('help-title').textContent,'Чем помочь?');
assert.deepEqual(opened,[]);
element('help-faq-open').onclick();
assert.equal(element('help-faq').hidden,false);
assert.equal(element('help-home').hidden,true);
element('help-faq-contact').onclick();
assert.deepEqual(opened,['https://t.me/lechatsergeev']);
assert.equal(element('help-manual').href,'https://t.me/lechatsergeev');
element('help-close').onclick();
assert.equal(element('help-panel').classList.contains('on'),false);
console.log('Miniapp help checks passed.');
