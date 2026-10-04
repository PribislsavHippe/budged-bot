"""Read a public Google Sheet schedule without storing its link or other staff rows."""
import csv
import io
import re
from urllib.parse import parse_qs, urlsplit

import httpx

import schedule

MAX_CSV_BYTES = 2_000_000
MONTH_NAMES = ('Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
               'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь')
OFF = {'', '-', 'х', 'x', 'п', 'и', 'в', 'вых', 'выходной', 'отпуск', 'off', 'бол', 'б'}


def link(text):
    """Return an opaque document ID and an optional selected tab ID."""
    match = re.search(r'https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+[^\s<>]*', text or '')
    if not match:
        raise ValueError('Пришли ссылку на Google Таблицу с графиком.')
    url = urlsplit(match.group(0).rstrip('.,)'))
    parts = url.path.split('/')
    if url.scheme != 'https' or url.hostname != 'docs.google.com' or len(parts) < 4 or parts[:3] != ['', 'spreadsheets', 'd']:
        raise ValueError('Пришли ссылку на Google Таблицу с графиком.')
    sheet_id = parts[3]
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', sheet_id):
        raise ValueError('Не разобрал адрес таблицы.')
    gids = parse_qs(url.query).get('gid') or parse_qs(url.fragment).get('gid') or []
    gid = gids[0] if gids else None
    if gid is not None and not re.fullmatch(r'\d{1,15}', gid):
        raise ValueError('Не разобрал лист таблицы.')
    return sheet_id, gid


async def read(sheet_id, month_key, gid=None):
    """Fetch one public CSV tab from the fixed Google Sheets host."""
    month = int(month_key[-2:])
    params = {'tqx': 'out:csv'}
    if gid is not None:
        params['gid'] = gid
    else:
        params['sheet'] = MONTH_NAMES[month - 1]
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq'
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            async with client.stream('GET', url, params=params) as response:
                if response.is_redirect:
                    raise ValueError('Не удалось открыть лист напрямую. Пришли фото графика.')
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_CSV_BYTES:
                        raise ValueError('Таблица слишком большая. Пришли фото своего графика.')
    except httpx.HTTPError as error:
        raise ValueError('Не получилось открыть таблицу. Проверь, что доступ по ссылке включён.') from error
    content = body.decode('utf-8-sig', errors='replace')
    if content.lstrip().startswith('<') or not content.strip():
        raise ValueError('Не получил данные листа. Проверь месяц и доступ по ссылке.')
    rows = list(csv.reader(io.StringIO(content)))
    if len(rows) > 300 or any(len(row) > 100 for row in rows):
        raise ValueError('Таблица слишком большая. Пришли фото своего графика.')
    people = parse_rows(rows)
    if not people:
        raise ValueError('Не нашёл смены на листе этого месяца. Проверь месяц или пришли фото графика.')
    return people


def _days(row):
    cells = []
    for col, raw in enumerate(row):
        value = raw.strip()
        if re.fullmatch(r'\d{1,2}', value) and 1 <= int(value) <= 31:
            cells.append((col, int(value)))
    # A heading must start at day 1 and continue in order. This excludes hour totals.
    if len(cells) >= 7 and [day for _, day in cells[:7]] == list(range(1, 8)):
        return cells
    return None


def parse_rows(rows):
    """Find repeated day headings and employee rows, including paired hour columns."""
    people = []
    days = None
    for row_number, row in enumerate(rows, 1):
        heading = _days(row)
        if heading:
            days = heading
            continue
        if not days:
            continue
        first_col = days[0][0]
        names = [value.strip() for value in row[:first_col] if value.strip()]
        if not names:
            continue
        name = names[-1]
        if not 1 <= len(name) <= 80 or not any(c.isalpha() for c in name):
            continue
        raw_cells = []
        invalid = []
        for col, day in days:
            value = row[col].strip() if col < len(row) else ''
            if value.casefold() in OFF:
                continue
            # Some rosters place a total of worked hours in the next day column.
            # An hour >= 24 cannot be a valid shift start.
            if value.isdecimal() and int(value) >= 24:
                continue
            try:
                schedule.cell(value)
            except ValueError:
                invalid.append(day)
            else:
                raw_cells.append({'day': day, 'text': value})
        if raw_cells:
            people.append({'name': name, 'row': row_number, 'cells': raw_cells, 'invalid': invalid})
    return people[:70]
