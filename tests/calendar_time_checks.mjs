import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const html = readFileSync(new URL('../webapp/index.html', import.meta.url), 'utf8');
const source = html.match(/function workClock\(iso\) \{[\s\S]*?\n\}/)?.[0];
assert.ok(source, 'calendar time formatter exists');
const workClock = runInNewContext(`(${source})`, { Date, Intl, Number });

assert.equal(workClock('2026-10-02T06:00:00+00:00'), '09:00');
assert.equal(workClock('2026-10-02T18:00:00+00:00'), '21:00');
assert.equal(workClock('2026-10-02T09:00:00+03:00'), '09:00');
assert.equal(workClock('2026-10-02T23:30:00+03:00'), '23:30');
assert.equal(workClock('2026-10-02T23:00:00+00:00'), '02:00');
assert.equal(workClock('invalid'), null);

const endSuggestion = html.match(/document\.getElementById\("day-start"\)\.onchange = (function \(\) \{[\s\S]*?\n\});/)?.[1];
assert.ok(endSuggestion, 'shift end suggestion exists');
const endField = { value: '' };
const suggestEnd = runInNewContext(`(${endSuggestion})`, {
  document: { getElementById: () => endField }, Number, Math, String
});
for (const [start, expected] of [['09:00', '21:00'], ['10:00', '22:00'], ['12:00', '23:00'], ['14:00', '23:00']]) {
  endField.value = '';
  suggestEnd.call({ value: start });
  assert.equal(endField.value, expected);
}
endField.value = '22:30';
suggestEnd.call({ value: '14:00' });
assert.equal(endField.value, '22:30', 'explicitly entered end is preserved');
