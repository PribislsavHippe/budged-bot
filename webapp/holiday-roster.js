// A separate editor over the two existing New Year drafts. Every write retains its original roster and revision.
(() => {
  'use strict';
  const G = window.ScheduleGrid;
  const ranges = [
    {start:'2026-12-16',end:'2026-12-31'},
    {start:'2027-01-01',end:'2027-01-15'},
  ];
  const dates = range => {
    const result = [];
    for (const day = new Date(range.start); day <= new Date(range.end); day.setUTCDate(day.getUTCDate() + 1)) {
      result.push(day.toISOString().slice(0, 10));
    }
    return result;
  };
  const days = ranges.flatMap(dates);
  const matches = name => ['итальянская','итали'].includes(String(name || '').trim().toLowerCase());

  function attach(root, normal, api, onClose) {
    const view = document.createElement('section');
    view.className = 're sg holiday-roster';
    view.hidden = true;
    view.innerHTML = `<div class="re-head">
      <h2 class="re-title">Новогодний график · ${G.period(days[0],days.at(-1))}</h2>
      <div class="re-actions">
        <button type="button" class="re-holiday" data-back>К обычному графику</button>
        <button type="button" class="re-build" data-build hidden>Собрать заново</button>
        <button type="button" class="btn" data-create hidden>Подготовить график</button>
      </div></div>
      <p class="re-error" role="alert"></p><p role="status" data-status hidden></p>
      <section class="sg-sheet" hidden><div class="sg-scroll"><table></table></div></section>`;
    root.appendChild(view);
    const q = selector => view.querySelector(selector);
    const sheet = q('.sg-sheet'), grid = q('table'), scroller = grid.parentElement;
    const build = q('[data-build]'), create = q('[data-create]'), back = q('[data-back]');
    const sc = G.scroller(grid,scroller,{sheet});
    const error = text => {q('.re-error').textContent = text || '';};
    const status = text => {q('[data-status]').textContent = text || '';q('[data-status]').hidden = !text;};
    let generation = 0, placeId, rows = [], people = [], busy = false, editing = null;

    const knownPeriod = (range, list) => list.find(p => p.start === range.start && p.end === range.end);
    const partFor = day => rows[day <= ranges[0].end ? 0 : 1];
    const sameView = current => generation === current && !view.hidden;
    function controls() {
      build.disabled = create.disabled = back.disabled = busy;
    }
    function close() {
      generation++;
      view.hidden = true;
      normal.hidden = false;
      editing = null;
    }
    back.onclick = () => {close();onClose();};

    async function load(periods) {
      const active = generation;
      busy = true;controls();error('');status('Загружаю график…');
      try {
        const found = ranges.map(range => knownPeriod(range,periods));
        const current = await Promise.all(found.map(async period => {
          if (!period) return null;
          const [draft, replies] = await Promise.all([api('open',{id:period.id}),api('offs',{id:period.id})]);
          if (draft.start !== period.start || draft.end !== period.end) {
            throw Error('Даты графика изменились. Открой новогодний график заново.');
          }
          return {draft,replies};
        }));
        if (!sameView(active)) return;
        rows = current;
        draw();
        status('');
      } catch (err) {
        if (sameView(active)) {error(err.message);status('');}
      } finally {
        if (sameView(active)) {busy = false;controls();}
      }
    }
    async function open(restaurantId) {
      generation++;
      const active = generation;
      normal.hidden = true;
      view.hidden = false;
      sheet.hidden = build.hidden = create.hidden = true;
      rows = [];people = [];editing = null;placeId = restaurantId;
      busy = true;controls();error('');status('Загружаю график…');
      try {
        const {periods} = await api('list',{restaurant_id:restaurantId,holiday:true});
        if (sameView(active)) await load(periods);
      } catch (err) {
        if (sameView(active)) {error(err.message);status('');busy = false;controls();}
      }
    }
    function draw() {
      const named = new Map();
      for (const [index,row] of rows.entries()) {
        if (!row) continue;
        const {draft:current,replies} = row, draft = current.draft;
        const active = new Set(current.team.map(person => person.id));
        const answered = new Set(replies.answered_links || []);
        for (const person of draft.people) {
          if (!active.has(person.link)) continue;
          const {link,name} = person;
          if (!named.has(link)) named.set(link,{name,cells:{},off:[],asked:[],wish:[],parts:[]});
          const entry = named.get(link), required = replies.requests?.[name] || [];
          entry.parts[index] = {name,answered:answered.has(link),required};
          Object.assign(entry.cells,draft.cells?.[name]);
          entry.off.push(...(draft.off?.[name] || []));
          entry.asked.push(...required);
          entry.wish.push(...(draft.wish_dates?.[name] || []),...(replies.desired?.[name] || []),
            ...dates(ranges[index]).filter(day => (replies.weekdays?.[name] || []).includes((G.at(day).getDay()+6)%7)));
        }
      }
      people = [...named.values()];
      const indexOf = day => day <= ranges[0].end ? 0 : 1;
      function count(day, predicate) {
        const index = indexOf(day);
        return rows[index] ? people.filter(person => person.parts[index] && predicate(person.parts[index])).length : '—';
      }
      const events = Object.assign({}, ...rows.filter(Boolean).map(row => row.draft.draft.events));
      const holidays = Object.assign({}, ...rows.filter(Boolean).map(row => row.draft.holidays));
      const left = scroller.scrollLeft;
      grid.innerHTML = G.table({days,people,events,holidays,counts:true,weekGap:false,scrolls:true,
        editCell:(person,day) => !!person.parts[indexOf(day)],
        totals:[
          {label:'Ответили без обязательного выходного',value:day => count(day,p => p.answered && !p.required.includes(day))},
          {label:'Не ответили',value:day => count(day,p => !p.answered)},
        ]});
      sheet.hidden = !rows.some(Boolean);
      build.hidden = !rows.every(Boolean);
      create.hidden = rows.every(Boolean);
      if (!sheet.hidden) {sc.size();scroller.scrollLeft = left;}
    }

    create.onclick = async () => {
      const active = generation, restaurant = placeId;
      busy = true;controls();error('');status('Готовлю график…');
      try {
        const data = await api('holiday_prepare',{restaurant_id:restaurant});
        if (sameView(active)) await load(data.periods);
      } catch (err) {
        if (sameView(active)) {error(err.message);status('');busy = false;controls();}
      }
    };

    build.onclick = async () => {
      if (!confirm('Собрать заново? Варианты и смены в таблице заменятся, выходные, поставленные вручную, останутся.')) return;
      const active = generation;
      busy = true;controls();error('');status('Собираю варианты…');
      for (const row of rows) {
        if (!sameView(active)) return;
        const {id,revision,draft} = row.draft;
        try {
          const updated = await api('build',{id,revision,draft},120000);
          row.draft = {...row.draft,...updated};
        } catch (err) {
          if (sameView(active)) error(G.period(row.draft.start,row.draft.end) + '. ' + err.message);
          break;
        }
      }
      if (sameView(active)) {draw();status('');busy = false;controls();}
    };

    function edit(cell) {
      if (busy || editing || !cell || cell.hasAttribute('aria-disabled')) return;
      const day = cell.dataset.day, person = people[Number(cell.dataset.r)], row = partFor(day);
      const entry = person.parts[day <= ranges[0].end ? 0 : 1];
      const old = row.draft.draft.cells?.[entry.name]?.[day] || '';
      const input = document.createElement('input');
      input.type = 'text';input.value = old;input.placeholder = '9–17';
      input.setAttribute('aria-label',person.name + ', ' + G.dayName(day));
      const restore = () => {
        if (editing !== input) return;
        editing = null;draw();
        grid.querySelector(`[data-r="${cell.dataset.r}"][data-day="${day}"]`)?.focus({preventScroll:true});
      };
      const commit = async () => {
        if (editing !== input || busy) return;
        const value = input.value.trim().replace(/[–—]/g,'-');
        if (value === old) {restore();return;}
        const active = generation;
        const draft = JSON.parse(JSON.stringify(row.draft.draft));
        const cells = {...draft.cells[entry.name]};
        if (value) cells[day] = value;else delete cells[day];
        draft.cells = {...draft.cells,[entry.name]:cells};
        busy = true;controls();input.readOnly = true;error('');status('Сохраняю…');
        try {
          const updated = await api('save',{id:row.draft.id,revision:row.draft.revision,draft});
          row.draft = {...row.draft,...updated};
          if (sameView(active)) {restore();status('Сохранено.');}
        } catch (err) {
          if (sameView(active)) {error(err.message);status('');input.readOnly = false;}
        } finally {
          if (sameView(active)) {busy = false;controls();}
        }
      };
      input.addEventListener('keydown',event => {
        if (event.key === 'Enter') {event.preventDefault();commit();}
        if (event.key === 'Escape' && !busy) {event.stopPropagation();restore();}
      });
      input.addEventListener('blur',commit);
      editing = input;cell.replaceChildren(input);input.focus();input.select();
    }
    grid.addEventListener('click',event => edit(event.target.closest('td.sg-c')));
    grid.addEventListener('keydown',event => {
      if (event.key === 'Enter' && event.target.matches('td.sg-c')) {event.preventDefault();edit(event.target);}
    });
    return {open,close};
  }
  window.HolidayRoster = {attach,matches};
})();
