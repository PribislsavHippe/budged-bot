"""Собирает живое демо нынешнего мини-приложения для портфолио (вкладки «Деньги» и «График»).

Настоящие файлы webapp/ копируются как есть. Вместо сервера и Telegram — заглушка demo-stub.js:
- личный журнал (privateMoney) включён и держит вымышленные записи в памяти, поэтому суммы, календарь
  и периоды считает настоящий код private-finance.js — так же, как у человека с журналом на телефоне;
- запросы к серверу отвечают метаданными: смены, время смен, выдуманный график ресторана.
Вкладки «План», «Ресторан», «UX Research» и их скрипты в демо не попадают.

Запуск: .venv/bin/python docs/prototypes/build_portfolio_demo.py [папка портфолио]
По умолчанию пишет в ~/Downloads/lechatsergeev.github.io/demos/money/.

Заодно собирает demos/days-off/: настоящая страница календаря выходных (days_off_app.PAGE) с настоящей
разметкой виджета (days_off_widget.HTML); сбор на 1–15 декабря, «Сохранить» ничего не отправляет.
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTFOLIO = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/Downloads/lechatsergeev.github.io')
OUT = os.path.join(PORTFOLIO, 'demos', 'money')
ASSETS = ['compat.js', 'background.js', 'glass.css', 'private-finance.js', 'tips.js', 'tips.css',
          'schedule-grid.js', 'schedule.js', 'schedule.css']
DROP_SCRIPTS = ['ux.js', 'private.js', 'sales.js', 'restaurant.js', 'research.js']
DROP_STYLES = ['sales.css', 'restaurant.css', 'research.css']
# Рамка телефона в портфолио уже настоящего экрана: рисуем страницу на ширине iPhone (402) и уменьшаем целиком,
# как в демо «Графика», иначе на телефоне кнопки сплющиваются, а подписи переносятся.
FIT = ('<script>(function(){function fit(){var w=window.innerWidth;'
       'document.documentElement.style.zoom=w<402?String(w/402):"";}fit();addEventListener("resize",fit);})();</script>')

STUB = r'''/* Демо для портфолио: вместо Telegram и сервера — вымышленный официант Вика, ноябрь 2026.
   Деньги считает настоящий код бота (private-finance.js) по записям ниже. */
(function () {
  var noop = function () {};
  var TODAY = "2026-11-19";
  window.Telegram = { WebApp: { initData: "", initDataUnsafe: { user: { id: 1 } }, platform: "unknown", version: "8.0",
    ready: noop, expand: noop, close: noop, openLink: noop, openTelegramLink: noop, sendData: noop, onEvent: noop, offEvent: noop,
    isVersionAtLeast: function () { return false; }, requestFullscreen: noop, showAlert: function (t, cb) { if (cb) cb(); },
    showConfirm: function (t, cb) { if (cb) cb(true); }, showPopup: function (p, cb) { if (cb) cb("ok"); },
    HapticFeedback: { impactOccurred: noop, notificationOccurred: noop, selectionChanged: noop },
    BackButton: { show: noop, hide: noop, onClick: noop, offClick: noop },
    MainButton: { show: noop, hide: noop, setText: noop, onClick: noop, offClick: noop } } };

  // Выдуманный график ресторана — тот же, что в демо вкладки «График».
  var NAMES = ["Аня", "Боря", "Вика", "Гоша", "Даша", "Ева", "Женя", "Зоя", "Ира", "Костя", "Лёва"], ME = 2;
  function days(a, b) { var out = [], x = new Date(a + "T12:00:00"), e = new Date(b + "T12:00:00");
    for (; x <= e; x.setDate(x.getDate() + 1)) out.push(x.toISOString().slice(0, 10)); return out; }
  function roster(startDay, endDay, seed) {
    var list = days(startDay, endDay);
    var people = NAMES.map(function (name, i) { return { name: name, me: i === ME, cells: {}, off: [] }; });
    list.forEach(function (day, k) {
      var wd = new Date(day + "T12:00:00").getDay(), weekend = wd === 0 || wd === 5 || wd === 6, need = weekend ? 6 : 5;
      var on = people.map(function (p, i) { return { p: p, i: i, w: ((k + i + seed) % 4) < 2 }; })
        .sort(function (a, b) { return (b.w - a.w) || ((a.i * 7 + k) % 11 - (b.i * 7 + k) % 11); }).slice(0, need);
      var starts = weekend ? ["10", "10", "11", "12", "12", "14"] : ["10", "11", "12", "12", "14"];
      on.forEach(function (x, j) { x.p.cells[day] = starts[j]; });
    });
    return { start: startDay, end: endDay, days: list, people: people, events: {}, holidays: seed ? {} : { "2026-11-04": "День народного единства" } };
  }
  var PERIODS = { "2026-11-01": roster("2026-11-01", "2026-11-15", 0), "2026-11-16": roster("2026-11-16", "2026-11-30", 1) };

  function iso(d) { return d.toISOString().slice(0, 10); }
  function rnd(seed) { var x = Math.sin(seed) * 10000; return x - Math.floor(x); }
  // Смены 2/2 с сентября по декабрь; чай — только в прошедшие смены.
  var shifts = [], planned = [], entries = [], id = 1;
  for (var d = new Date("2026-09-01T12:00:00Z"); d <= new Date("2026-12-31T12:00:00Z"); d.setUTCDate(d.getUTCDate() + 1)) {
    var day = iso(d), k = Math.round((d - new Date("2026-09-01T12:00:00Z")) / 864e5);
    // В ноябре смены Вики берутся из графика ресторана, чтобы «Деньги» и «График» совпадали.
    var fromRoster = day.slice(0, 7) === "2026-11" ? (PERIODS[day < "2026-11-16" ? "2026-11-01" : "2026-11-16"].people[ME].cells[day]) : null;
    if (day.slice(0, 7) === "2026-11" ? !fromRoster : k % 4 >= 2) continue;
    shifts.push(day);
    var hour = fromRoster ? parseInt(fromRoster, 10) : [10, 11, 12][k % 3];
    var start = String(hour).padStart(2, "0") + ":00", end = String(Math.min(hour + 12, 23)) + ":00";
    planned.push({ shift_date: day, starts_at: start + ":00", ends_at: end + ":00" });
    if (day > TODAY) continue;
    var r = rnd(k + 7), tips = Math.round((1500 + r * 3600) / 50) * 50, card = Math.round(tips * (0.3 + rnd(k) * 0.4) / 50) * 50;
    var at = day + "T20:00:00Z";
    entries.push({ id: id++, kind: "income", category: "Чаевые", account: "cash", signed_amount: String(tips - card), work_date: day, created_at: at, note: "" });
    if (card) entries.push({ id: id++, kind: "income", category: "Чаевые", account: "card", signed_amount: String(card), work_date: day, created_at: at, note: "" });
    if (rnd(k + 3) > 0.6) entries.push({ id: id++, kind: "expense", category: "Такси", account: "cash", signed_amount: String(-Math.round((350 + rnd(k + 5) * 500) / 10) * 10), work_date: day, created_at: at, note: "такси" });
  }
  var state = { active: true, ready: false, lost: false, lostReason: null, entries: entries, receipts: [],
    exitArchivePending: false, exitConflict: false, exiting: false, archiveCount: 0, today: TODAY };
  function find(x) { return entries.findIndex(function (e) { return String(e.id) === String(x); }); }
  window.privateMoney = { state: state,
    init: function () { state.ready = true; return Promise.resolve(state); },
    add: function (rec) { rec.work_date = rec.work_date || TODAY; rec.created_at = rec.created_at || new Date().toISOString(); entries.push(rec); return Promise.resolve(true); },
    remove: function (x) { var i = find(x); if (i >= 0) entries.splice(i, 1); return Promise.resolve(true); },
    edit: function (x, patch) { var i = find(x); if (i >= 0) Object.assign(entries[i], patch); return Promise.resolve(true); },
    sync: function () { return Promise.resolve(0); }, importUrl: function () { return Promise.resolve(null); },
    clear: function () { entries.length = 0; return Promise.resolve(); }, restoreRecent: function () { return Promise.resolve(0); },
    activate: function () { return Promise.resolve(); }, deactivate: function () { return Promise.resolve(); },
    finishExitArchive: function () { return Promise.resolve(); }, rotate: function () { return Promise.resolve(); },
    request: function () { return Promise.resolve({}); }, available: function () { return true; },
    get active() { return true; }, get entries() { return entries; }, get payload() { return {}; } };

  function meta(year, month) {
    var first = year + "-" + String(month).padStart(2, "0") + "-01", last = first.slice(0, 8) + new Date(Date.UTC(year, month, 0)).getUTCDate();
    var inMonth = function (s) { return s.shift_date >= first && s.shift_date <= last; };
    return { year: year, month: month, private_money_mode: true, operational_today: TODAY, calendar_shift_dates: shifts,
      scheduled_shifts: shifts.filter(function (s) { return s.slice(0, 7) === first.slice(0, 7); }),
      planned_times: planned.filter(inMonth).filter(function (s) { return s.shift_date >= TODAY; }),
      worked_times: planned.filter(inMonth).filter(function (s) { return s.shift_date < TODAY; })
        .map(function (s) { return { shift_date: s.shift_date, actual_start: s.shift_date + "T" + s.starts_at + "+03:00", actual_end: s.shift_date + "T" + s.ends_at + "+03:00" }; }),
      calendar_wishes: {}, days_off_beta: false, tip_entries: [], bot_username: null, shift_goal: null };
  }
  function ok(json) { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(json); } }); }
  window.fetch = function (url, o) {
    var body = {}; try { body = JSON.parse((o && o.body) || "{}"); } catch (e) {}
    var path = String(url).split("?")[0];
    if (path === "/api/stats") return ok(meta(2026, 11));
    if (path === "/api/month") return ok(meta(+body.year, +body.month));
    if (path === "/api/schedule/view") {
      var p = PERIODS[body.start || "2026-11-16"] || PERIODS["2026-11-16"];
      return ok({ available: true, today: TODAY, restaurant_id: 1, restaurants: [{ id: 1, name: "Ресторан" }],
        periods: [{ start: "2026-11-01", end: "2026-11-15" }, { start: "2026-11-16", end: "2026-11-30" }],
        period: p, can_edit: false, site_url: null, mine: { requests: [], weekdays: [], pattern: "2/2" } });
    }
    if (path === "/api/tips_range") {
      // Как на сервере: часы — по записанным сменам периода, будущие дни не считаются; деньги посчитает private-finance.js.
      var custom = "start" in body || "end" in body, has = shifts.indexOf(TODAY) >= 0;
      var tipDays = entries.filter(function (e) { return e.kind === "income" && e.work_date <= TODAY; }).map(function (e) { return e.work_date; }).sort();
      var s = custom ? body.start : (has || !tipDays.length ? TODAY : tipDays[tipDays.length - 1]), e = custom ? body.end : s;
      if (e > TODAY) e = TODAY;
      var worked = planned.filter(function (x) { return x.shift_date >= s && x.shift_date <= e && x.shift_date < TODAY; }).map(function (x) {
        return { date: x.shift_date, hours: (parseInt(x.ends_at) - parseInt(x.starts_at)) }; });
      return ok({ today: TODAY, custom: custom, has_shift: custom ? (s === e && shifts.indexOf(s) >= 0) : has,
        hours: worked.reduce(function (n, w) { return n + w.hours; }, 0), worked: worked, start: s, end: e });
    }
    if (path === "/api/iphone_calendar") return ok({ error: "В демо календарь не подключается." });
    return ok({});
  };
})();
'''


def build_days_off():
    """Страница выходных: вместо сервера — ответы в самой странице."""
    import json
    from datetime import date
    from unittest.mock import patch
    sys.path.insert(0, ROOT)
    patch('supabase.create_client').start()
    import days_off_app
    import days_off_widget as widget
    today = date(2026, 11, 19)
    row = {**days_off_app.demo_period(today), 'id': 'dddddddd-0000-4000-8000-000000000001'}
    state = {'demo': False, 'caption': widget.short_caption(row), 'note': None,
             'chat_data': widget.state(row, [], [], today), 'html': widget.HTML}
    stub = ('<meta name="robots" content="noindex">\n' + FIT + '\n<script>\n'
            '/* Демо для портфолио: настоящая страница календаря выходных, ответы сервера — здесь же. */\n'
            '(function(){var S=' + json.dumps(state, ensure_ascii=False).replace('</', '<\\/') + ';\n'
            'window.fetch=function(u){var b=String(u).indexOf("/save")>=0?{result:"ok",chat_data:null}:S;'
            'return Promise.resolve({ok:true,json:function(){return Promise.resolve(b)}})}})();\n</script>')
    page = days_off_app.PAGE.replace('<script src="https://telegram.org/js/telegram-web-app.js"></script>', stub)
    if stub not in page:
        raise SystemExit('Не нашёл подключение Telegram в days_off_app.PAGE')
    out = os.path.join(PORTFOLIO, 'demos', 'days-off')
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page)
    print(out)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(os.path.join(ROOT, 'webapp', name), os.path.join(OUT, name))
    page = open(os.path.join(ROOT, 'webapp', 'index.html'), encoding='utf-8').read()
    page = page.replace('<script src="https://telegram.org/js/telegram-web-app.js"></script>',
                        '<meta name="robots" content="noindex">\n' + FIT + '\n<script src="demo-stub.js"></script>')
    for name in DROP_SCRIPTS:
        page = page.replace(f'<script src="/app/{name}"></script>\n', '')
    for name in DROP_STYLES:
        page = page.replace(f'<link rel="stylesheet" href="/app/{name}">\n', '')
    page = page.replace('/app/', '')
    # Без «Плана», «Ресторана» и «UX Research»: их нет в кейсе.
    page = re.sub(r'\s*<button id="tab-(sales|restaurant|research)"[^>]*>[^<]*</button>', '', page)
    page = re.sub(r'\s*<section id="(sales|restaurant|research)-panel"[^>]*></section>', '', page)
    leftovers = [n for n in DROP_SCRIPTS if n in page]
    if leftovers or 'tab-sales' in page:
        raise SystemExit('В странице остались лишние части: ' + ', '.join(leftovers or ['tab-sales']))
    with open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page)
    with open(os.path.join(OUT, 'demo-stub.js'), 'w', encoding='utf-8') as f:
        f.write(STUB)
    print(OUT)
    build_days_off()


if __name__ == '__main__':
    main()
