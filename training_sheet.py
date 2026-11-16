"""Таблица записи на тренинги (Google Sheets): что за тренинги, когда, где свободная строка для записи.

Таблицу ведут вручную, и она неровная: блок на тренинг — строка «Название активности | Ведущий», под ней название
и ведущий, ниже «Список сотрудников на 18.11 с 12:00 до 14:30» (бывает «07.12 с 11:00-13:00»), «ОТМЕНЕНО»,
шапка «ФИО | Ресторан | Должность» и пронумерованные строки. Несколько занятий одного блока идут вправо
(у «Вина» — по четыре колонки). Строки сотрудников других ресторанов не сохраняются: имена нужны только в памяти,
чтобы узнать, записан ли уже человек из нашего графика.
"""
import base64
import json
import os
import re
import time
from datetime import date

import httpx

ACTIVITY = 'название активности'
DATE = re.compile(r'(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?!\d)')
TIME = re.compile(r'(\d{1,2})[:.](\d{2})\s*(?:-|–|—|до)\s*(\d{1,2})[:.](\d{2})')
SHEET = re.compile(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,100})')
SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
API = 'https://sheets.googleapis.com/v4/spreadsheets/'
MAX_ROWS = 400
MAX_COLS = 60


def sheet_id(text):
    found = SHEET.search(text or '')
    return found[1] if found else None


def _cell(rows, r, c):
    return str(rows[r][c]).strip() if 0 <= r < len(rows) and 0 <= c < len(rows[r]) else ''


def _year(day, month, today):
    """Год без подписи: ближайший к сегодняшнему дню (таблица — на месяц вперёд, прошлые занятия остаются)."""
    options = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            options.append(date(year, month, day))
        except ValueError:
            pass
    return min(options, key=lambda d: abs((d - today).days)) if options else None


def _when(text, today):
    found, hours = DATE.search(text), TIME.search(text)
    if not found or not hours:
        return None
    day, month, year = int(found[1]), int(found[2]), found[3]
    try:
        when = date(int(year) + (2000 if len(year) == 2 else 0), month, day) if year else _year(day, month, today)
        start, end = int(hours[1]) * 60 + int(hours[2]), int(hours[3]) * 60 + int(hours[4])
    except ValueError:
        return None
    if when is None or not 0 <= start < end <= 24 * 60:
        return None
    return when, start, end


def sessions(tab, rows, today):
    """Занятия листа: [{'key', 'tab', 'title', 'activity', 'host', 'date', 'start', 'end', 'cancelled',
    'col', 'slots': [строки для записи], 'names': [кто записан]}]. Без даты и времени занятие не возвращается."""
    rows = [list(r[:MAX_COLS]) for r in rows[:MAX_ROWS]]
    starts = [r for r in range(len(rows)) if any(str(v).strip().lower() == ACTIVITY for v in rows[r])]
    found = []
    for n, top in enumerate(starts):
        bottom = starts[n + 1] if n + 1 < len(starts) else len(rows)
        title = _cell(rows, top, 0)
        for col, value in enumerate(rows[top]):
            if str(value).strip().lower() != ACTIVITY:
                continue
            activity, host = _cell(rows, top + 1, col), _cell(rows, top + 1, col + 1)
            when, header, cancelled = None, None, False
            for r in range(top + 1, bottom):
                text = ' '.join(_cell(rows, r, c) for c in range(col, col + 3))
                if when is None and (moment := _when(text, today)):
                    when = moment
                if 'отмен' in text.lower():
                    cancelled = True
                if header is None and _cell(rows, r, col).lower() == 'фио':
                    header = r
            if when is None or header is None:
                continue
            slots, names = [], []
            for r in range(header + 1, bottom):
                numbered = _cell(rows, r, col - 1).isdigit()
                name = _cell(rows, r, col)
                if not numbered and not name:
                    if slots or names:
                        break
                    continue
                if name:
                    names.append(name)
                elif numbered:
                    slots.append(r)
            day, start, end = when
            label = activity or title
            found.append({'key': f'{day.isoformat()}|{start}|{_norm(label)[:40]}', 'tab': tab,
                          'title': title, 'activity': label, 'host': host, 'date': day,
                          'start': start, 'end': end, 'cancelled': cancelled, 'col': col,
                          'slots': slots, 'names': names})
    return found


def _norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^0-9a-zа-я ]', ' ', str(text).lower().replace('ё', 'е'))).strip()


def same_person(written, name):
    """«Сергеев Алексей» в таблице и «Алексей Сергеев» в графике — один человек; «Леся Б» — нет уверенности."""
    a, b = set(_norm(written).split()), set(_norm(name).split())
    return len(a) >= 2 and a == b


def column(index):
    out = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        out = chr(65 + rest) + out
    return out


# --- Доступ к Google -------------------------------------------------------------------------------------------

_token = {'value': None, 'until': 0}


def configured():
    return bool(os.getenv('GOOGLE_SERVICE_ACCOUNT'))


def _account():
    raw = os.getenv('GOOGLE_SERVICE_ACCOUNT', '')
    if not raw.lstrip().startswith('{'):
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


async def _bearer(client):
    if _token['value'] and _token['until'] > time.time() + 60:
        return _token['value']
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    account = _account()
    now = int(time.time())
    head = _b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())
    claims = _b64(json.dumps({'iss': account['client_email'], 'scope': SCOPE, 'aud': account['token_uri'],
                              'iat': now, 'exp': now + 3600}).encode())
    key = serialization.load_pem_private_key(account['private_key'].encode(), password=None)
    signature = key.sign(f'{head}.{claims}'.encode(), padding.PKCS1v15(), hashes.SHA256())
    response = await client.post(account['token_uri'], data={
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': f'{head}.{claims}.{_b64(signature)}'})
    response.raise_for_status()
    body = response.json()
    _token.update(value=body['access_token'], until=now + int(body.get('expires_in', 3600)))
    return _token['value']


async def read(document, today):
    """Все листы таблицы → занятия. Без сервисного аккаунта — открытая по ссылке таблица через CSV."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        if configured():
            headers = {'Authorization': 'Bearer ' + await _bearer(client)}
            meta = await client.get(API + document, params={'fields': 'sheets.properties(title,hidden)'}, headers=headers)
            meta.raise_for_status()
            tabs = [s['properties']['title'] for s in meta.json().get('sheets', []) if not s['properties'].get('hidden')]
            if not tabs:
                return []
            values = await client.get(API + document + '/values:batchGet', headers=headers,
                                      params=[('ranges', _quote(t)) for t in tabs[:12]])
            values.raise_for_status()
            ranges = values.json().get('valueRanges', [])
            return [s for tab, part in zip(tabs, ranges) for s in sessions(tab, part.get('values', []), today)]
        return await _read_public(client, document, today)


async def _read_public(client, document, today):
    import csv
    import io
    page = await client.get(f'https://docs.google.com/spreadsheets/d/{document}/htmlview')
    page.raise_for_status()
    tabs = re.findall(r'items\.push\(\{name: "((?:[^"\\]|\\.)*)", pageUrl: "[^"]*gid=(\d+)', page.text)
    if not tabs:
        tabs = [('', g) for g in dict.fromkeys(re.findall(r'gid: "(\d+)"', page.text))]
    found = []
    for title, gid in tabs[:12]:
        response = await client.get(f'https://docs.google.com/spreadsheets/d/{document}/export',
                                    params={'format': 'csv', 'gid': gid})
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            continue
        found += sessions(title, list(csv.reader(io.StringIO(response.text))), today)
    return found


def _quote(tab):
    return "'" + tab.replace("'", "''") + "'"


async def write(document, session, values, today):
    """Вписать человека в первую свободную строку занятия. Таблицу перечитывает прямо перед записью:
    её правят руками, и строки могли сдвинуться. Возвращает 'ok', 'full', 'gone' или 'already'."""
    async with httpx.AsyncClient(timeout=20) as client:
        headers = {'Authorization': 'Bearer ' + await _bearer(client)}
        response = await client.get(API + document + '/values/' + _quote(session['tab']), headers=headers)
        response.raise_for_status()
        fresh = next((s for s in sessions(session['tab'], response.json().get('values', []), today)
                      if s['key'] == session['key']), None)
        if fresh is None or fresh['cancelled']:
            return 'gone'
        if any(same_person(name, values[0]) for name in fresh['names']):
            return 'already'
        if not fresh['slots']:
            return 'full'
        row, col = fresh['slots'][0] + 1, fresh['col']
        target = f"{_quote(session['tab'])}!{column(col)}{row}:{column(col + len(values) - 1)}{row}"
        update = await client.put(API + document + '/values/' + target, headers=headers,
                                  params={'valueInputOption': 'RAW'}, json={'values': [values]})
        update.raise_for_status()
        return 'ok'
