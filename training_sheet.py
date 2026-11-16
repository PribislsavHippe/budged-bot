"""Таблица записи на тренинги (Google Sheets): что за тренинги, когда, где свободная строка для записи.

Таблицу ведут вручную, и она неровная: блок на тренинг — строка «Название активности | Ведущий», под ней название
и ведущий, ниже «Список сотрудников на 18.11 с 12:00 до 14:30» (бывает «07.12 с 11:00-13:00»), «ОТМЕНЕНО»,
шапка «ФИО | Ресторан | Должность» и пронумерованные строки. Несколько занятий одного блока идут вправо
(у «Вина» — по четыре колонки). Строки сотрудников других ресторанов не сохраняются: имена нужны только в памяти,
чтобы узнать, записан ли уже человек из нашего графика. Записываются люди сами: бот даёт ссылку прямо на свободную
строку занятия. Таблица должна быть открыта по ссылке хотя бы на просмотр.
"""
import csv
import io
import re
from datetime import date

import httpx

ACTIVITY = 'название активности'
DATE = re.compile(r'(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?!\d)')
TIME = re.compile(r'(\d{1,2})[:.](\d{2})\s*(?:-|–|—|до)\s*(\d{1,2})[:.](\d{2})')
SHEET = re.compile(r'https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,100})')
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


def sessions(tab, rows, today, gid=None):
    """Занятия листа: [{'key', 'tab', 'gid', 'title', 'activity', 'host', 'date', 'start', 'end', 'cancelled',
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
            found.append({'key': f'{day.isoformat()}|{start}|{_norm(label)[:40]}', 'tab': tab, 'gid': gid,
                          'title': title, 'activity': label, 'host': host, 'date': day,
                          'start': start, 'end': end, 'cancelled': cancelled, 'col': col, 'header': header,
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


def link(document, session):
    """Ссылка на таблицу: сразу нужный лист и первая свободная строка занятия (или шапка, если строк нет)."""
    url = f'https://docs.google.com/spreadsheets/d/{document}/edit'
    if session.get('gid') is None:
        return url
    row = (session['slots'][0] if session.get('slots') else session.get('header', 0)) + 1
    return f"{url}#gid={session['gid']}&range={column(session['col'])}{row}"


async def read(document, today):
    """Все листы открытой по ссылке таблицы → занятия. Листы — месяцы одного файла; новые находятся сами."""
    import httpx
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
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
            found += sessions(title, list(csv.reader(io.StringIO(response.text))), today, gid)
        return found
