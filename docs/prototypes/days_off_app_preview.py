"""Локальный просмотр страницы мини-приложения «Выходные» (`days_off_app.PAGE`) без Telegram и базы.

Запуск: .venv/bin/python docs/prototypes/days_off_app_preview.py — затем http://localhost:8766/app/days-off.
Сбор выходных изображается на ближайшие полмесяца; «Сохранить» запоминает выбор в памяти процесса.
"""
import json
import os
import sys
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
patch('supabase.create_client').start()

from aiohttp import web  # noqa: E402

import days_off_app  # noqa: E402
import days_off_widget as widget  # noqa: E402

TODAY = date.today()
ROW = {**days_off_app.demo_period(TODAY), 'id': 'dddddddd-0000-4000-8000-000000000001'}
SAVED = {'days': [], 'desired': [], 'hours': {}, 'at': None}


def chat_data():
    return widget.state(ROW, SAVED['days'], SAVED['desired'], TODAY, SAVED['at'], SAVED['hours'])


async def state(request):
    return web.json_response({'demo': False, 'caption': widget.short_caption(ROW), 'note': None,
                              'chat_data': chat_data(), 'html': widget.HTML})


async def save(request):
    body = await request.json()
    parsed = widget.parse(body.get('data'))
    if parsed is None:
        return web.json_response({'result': 'Обнови страницу.', 'chat_data': None})
    start = date.fromisoformat(ROW['start_date'])
    pick = lambda mask: [(start + timedelta(days=i)).isoformat() for i in range(31) if mask >> i & 1]
    SAVED.update(days=pick(parsed['required']), desired=pick(parsed['preferred']),
                 hours=widget.hours_for(ROW, parsed, TODAY), at='только что')
    return web.json_response({'result': 'ok', 'chat_data': chat_data()})


app = web.Application()
app.router.add_get('/app/days-off', days_off_app.page)
app.router.add_post('/api/days_off/state', state)
app.router.add_post('/api/days_off/save', save)

if __name__ == '__main__':
    web.run_app(app, port=int(os.getenv('PORT', '8766')))
