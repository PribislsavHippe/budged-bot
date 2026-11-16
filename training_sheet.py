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
# Время ищется после даты: «с 12:00 до 14:30», «11:00-14:30», «с 11 до 14».
TIME = re.compile(r'(\d{1,2})(?:[:.](\d{2}))?\s*(?:-|–|—|до)\s*(\d{1,2})(?:[:.](\d{2}))?')
MONTH_STEMS = ('январ', 'феврал', 'март', 'апрел', 'ма[йя]', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр')
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


def tab_month(tab):
    """Месяц листа из названия: «ЗАПИСЬ НА ТРЕНИНГИ (НОЯБРЬ)26» → (2026, 11). Без месяца — None."""
    low = str(tab).lower()
    month = next((i + 1 for i, stem in enumerate(MONTH_STEMS) if re.search(stem, low)), None)
    year = re.findall(r'(?<!\d)(\d{2}|\d{4})(?!\d)', low)
    if month is None or not year:
        return None
    value = int(year[-1])
    return (value + 2000 if value < 100 else value), month


def _when(text, today, month=None):
    """(дата, начало, конец) в минутах. None — нет даты или времени. 'old' — дата не из месяца листа:
    хвост, скопированный с прошлого листа (у «Вина» на листах ноября и декабря остались октябрьские занятия)."""
    found = DATE.search(text)
    hours = TIME.search(text, found.end()) if found else None
    if not found or not hours:
        return None
    day, number, year = int(found[1]), int(found[2]), found[3]
    try:
        if year:
            when = date(int(year) + (2000 if len(year) == 2 else 0), number, day)
        elif month:
            # Год — тот, при котором дата ближе всего к месяцу листа.
            anchor = date(month[0], month[1], 15)
            when = min((date(y, number, day) for y in (anchor.year - 1, anchor.year, anchor.year + 1)),
                       key=lambda d: abs((d - anchor).days))
        else:
            when = _year(day, number, today)
        start = int(hours[1]) * 60 + int(hours[2] or 0)
        end = int(hours[3]) * 60 + int(hours[4] or 0)
    except ValueError:
        return None
    if when is None or not 6 * 60 <= start < end <= 24 * 60:
        return None
    if month and (when.year * 12 + when.month) - (month[0] * 12 + month[1]) not in (0, 1):
        return 'old'
    return when, start, end


def sessions(tab, rows, today, gid=None, problems=None):
    """Занятия листа: [{'key', 'tab', 'gid', 'title', 'activity', 'host', 'date', 'start', 'end', 'cancelled',
    'col', 'slots': [строки для записи], 'names': [кто записан]}]. Без даты и времени занятие не возвращается.
    problems — список, куда попадают строки с датой, которые не удалось прочитать (чтобы не молчать о них)."""
    rows = [list(r[:MAX_COLS]) for r in rows[:MAX_ROWS]]
    month = tab_month(tab)
    starts = [r for r in range(len(rows)) if any(str(v).strip().lower() == ACTIVITY for v in rows[r])]
    found = []
    for n, top in enumerate(starts):
        bottom = starts[n + 1] if n + 1 < len(starts) else len(rows)
        title = _cell(rows, top, 0)
        for col, value in enumerate(rows[top]):
            if str(value).strip().lower() != ACTIVITY:
                continue
            activity, host = _cell(rows, top + 1, col), _cell(rows, top + 1, col + 1)
            when, header, cancelled, dated = None, None, False, None
            for r in range(top + 1, bottom):
                text = ' '.join(_cell(rows, r, c) for c in range(col, col + 3))
                if when is None and header is None and DATE.search(text):
                    when, dated = _when(text, today, month), text
                if 'отмен' in text.lower():
                    cancelled = True
                if header is None and _cell(rows, r, col).lower() == 'фио':
                    header = r
            if when is None and dated and problems is not None:
                problems.append((tab, dated.strip()[:80]))
            if when in (None, 'old') or header is None:
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


async def read(document, today, problems=None):
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
            found += sessions(title, list(csv.reader(io.StringIO(response.text))), today, gid, problems)
        return found
