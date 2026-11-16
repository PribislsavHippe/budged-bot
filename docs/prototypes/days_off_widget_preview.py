"""Пересобрать docs/prototypes/days-off-widget-preview.html из days_off_widget.HTML: .venv/bin/python docs/prototypes/days_off_widget_preview.py"""
import html, json, sys
from datetime import date
sys.path.insert(0, '.')
from unittest.mock import patch
patch('supabase.create_client').start()
import days_off_widget as w
ROW = {'id': 'dddddddd-0000-0000-0000-000000000001', 'start_date': '2026-11-16', 'end_date': '2026-11-30'}
CAP = w.short_caption(ROW)
STUB = ("window.TelegramWidget={chatData:%s,request:function(q){document.title=q;return{then:function(ok)"
        "{setTimeout(function(){ok({result:'ok'})},400)}}},onEvent:function(){},ready:function(){}};")
def frame(data, after='', height=330):
    doc = ('<!doctype html><meta charset=utf-8><body style="margin:0"><script>' + STUB % json.dumps(data) + '</script>'
           + w.HTML + ('<script>' + after + '</script>' if after else ''))
    return f'<iframe style="height:{height}px" srcdoc="{html.escape(doc)}" scrolling="no"></iframe>'
states = [
    (w.state(ROW, [], [], date(2026, 11, 16)), '', 300, 'Первое открытие: ничего не отмечено'),
    (w.state(ROW, ['2026-11-18', '2026-11-19'], ['2026-11-25'], date(2026, 11, 18), hours={'2026-11-21': [540, 1020]}),
     'tap(5)', 350, 'Вых, жел и часы: 21-е выбрано, часы меняются «−» и «+»'),
    (w.state(ROW, ['2026-11-18', '2026-11-19'], ['2026-11-25'], date(2026, 11, 18), '18.11 в 14:05',
             hours={'2026-11-21': [540, 1020], '2026-11-28': [540, 1020]}), '', 320, 'После «Сохранить»: бот записал'),
]
out = ['<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Виджет: выходные</title>',
       '<!-- Предпросмотр виджета «Календарь выходных» (days_off_widget.HTML) в пузыре чата. Временный дизайн по принятой системе,',
       '     до макета владельца. Собран из текущей разметки; после правки HTML: .venv/bin/python docs/prototypes/days_off_widget_preview.py. -->',
       '<style>body{margin:0;background:#d9d9de;font:15px -apple-system,system-ui,sans-serif;display:flex;gap:24px;justify-content:center;flex-wrap:wrap;padding:24px}',
       '.m{width:360px}.b{background:#fff;border-radius:18px;padding:6px;box-shadow:0 4px 30px rgba(0,0,0,.12)}',
       'iframe{border:0;width:100%;display:block;border-radius:18px}',
       '.cap{padding:8px 10px 2px;font-size:15px;color:#000}.tm{display:block;text-align:right;color:#8a8a8e;font-size:12px;padding:0 10px 4px}',
       '.n{font-size:13px;color:#55575c;margin:8px 8px 0}</style>']
for data, after, height, note in states:
    out.append(f'<div class="m"><div class="b">{frame(data, after, height)}<div class="cap">{html.escape(CAP)}</div>'
               f'<span class="tm">12:00</span></div><div class="n">{note}</div></div>')
open('docs/prototypes/days-off-widget-preview.html', 'w').write('\n'.join(out) + '\n')
