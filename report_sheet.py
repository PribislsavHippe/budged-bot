"""Bounded, read-only public Sheets import. No Google login, LLM or stored URL."""
import calendar
import csv
import io
import json
import re
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from sales import validate_values, month_key
from schedule_sheet import link, MONTH_NAMES

ACCESS_ERROR = 'Не получилось открыть таблицу. Включи доступ по ссылке для просмотра или пришли скриншот'
MAX_BYTES = 2_000_000


class SheetParseError(ValueError):
    """Content-free reason suitable for an owner diagnostic."""
    pass


class Tabs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.names = []

    def handle_starttag(self, tag, attrs):
        if 'docs-sheet-tab-caption' in dict(attrs).get('class', '').split():
            self.capture = True

    def handle_data(self, data):
        if self.capture and data.strip():
            self.names.append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'div':
            self.capture = False


def tab_ids(page):
    """Read Google's public sheet metadata as JSON data, never execute scripts.

    This bootstrap format is undocumented. Fail closed if it changes: do not
    silently export the first tab, which can belong to a different month.
    """
    found = {}
    for match in re.finditer(r'\[21350203,("(?:\\.|[^"\\])*")\]', page):
        try:
            record = json.loads(json.loads(match[1]))
            gid = record[2]
            name = record[3][0]['1'][0][2]
            if not isinstance(name, str) or not isinstance(gid, str) or not re.fullmatch(r'\d{1,15}', gid):
                continue
            if name in found and found[name] != gid:
                raise SheetParseError('duplicate_sheet_names')
            found[name] = gid
        except (ValueError, TypeError, IndexError, KeyError) as error:
            if isinstance(error, SheetParseError):
                raise
            continue
    return found


def allowed_download(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    try:
        return (parsed.scheme == 'https' and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and
                (host == 'docs.google.com' or
                 re.fullmatch(r'doc-[a-z0-9-]+-sheets\.googleusercontent\.com', host) is not None))
    except ValueError:
        return False


async def fetch(sheet_id, *, sheet=None, directory=False):
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', sheet_id):
        raise ValueError(ACCESS_ERROR)
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit'
    params = None
    if not directory:
        metadata = await fetch(sheet_id, directory=True)
        gid = tab_ids(metadata).get(sheet)
        if gid is None:
            raise SheetParseError('sheet_metadata_unavailable')
        url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export'
        params = {'format': 'csv', 'gid': gid}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            # Google serves exports through a generated Sheets download host.
            # Validate every redirect before requesting it, with no cookies/auth.
            for hop in range(4):
                if not allowed_download(url):
                    raise ValueError(ACCESS_ERROR)
                async with client.stream('GET', url, params=params) as response:
                    if response.is_redirect:
                        location = response.headers.get('location')
                        if not location or hop == 3:
                            raise ValueError(ACCESS_ERROR)
                        url = urljoin(url, location)
                        params = None
                        continue
                    if response.status_code != 200:
                        raise ValueError(ACCESS_ERROR)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ValueError(ACCESS_ERROR)
                    return body.decode('utf-8-sig', errors='replace')
    except httpx.HTTPError:
        raise ValueError(ACCESS_ERROR) from None
    raise ValueError(ACCESS_ERROR)


async def tabs(sheet_id):
    parser = Tabs()
    parser.feed(await fetch(sheet_id, directory=True))
    names = list(dict.fromkeys(parser.names))
    if not names or len(names) > 70 or any(len(n) > 100 for n in names):
        raise ValueError(ACCESS_ERROR)
    return names


def sheet_month(name):
    value = name.casefold().strip()
    for i, month in enumerate(MONTH_NAMES, 1):
        match = re.fullmatch(re.escape(month.casefold()) + r'\s+(20\d{2}|\d{2})', value)
        if match:
            return month_key(f'{int(match[1]) + (2000 if len(match[1]) == 2 else 0):04d}-{i:02d}')
    return None


def norm(value):
    return re.sub(r'\s+', ' ', value.strip().casefold().replace('ё', 'е'))


def amount(value):
    value = re.sub(r'[\s\u00a0\u202f]', '', value).replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
        raise SheetParseError('invalid_layout_or_value')
    try:
        return validate_values({'turnover': value}, ('turnover',))['turnover']
    except ValueError:
        raise SheetParseError('invalid_amount') from None


def parse_rows(rows, month, today):
    """Import explicitly identifiable turnover only; unlabeled blocks are omitted.

    Periods 1–7, 8–14 are incremental. Periods 1–7, 1–14 are snapshots.
    Never fill blanks, import goals/remainders/bonuses, or infer other metrics.
    """
    if not rows or len(rows) > 300 or any(len(r) > 100 for r in rows):
        raise SheetParseError('invalid_layout_or_value')
    year, mon = map(int, month.split('-'))
    last = calendar.monthrange(year, mon)[1]
    heading = next((i for i, row in enumerate(rows) if row and norm(row[0]) == 'официант' and
                    any(re.fullmatch(r'продажи\s+\d{1,2}\s*[-–—]\s*\d{1,2}', norm(c)) for c in row)), None)
    if heading is None:
        return []
    end = next((i for i in range(heading+1, len(rows)) if rows[i] and norm(rows[i][0]) == 'официант'), len(rows))
    block = rows[heading:end]
    if not any('план оборот' in norm(' '.join(row)) or 'товарооборот' in norm(' '.join(row)) for row in block):
        return []
    periods = []
    for col, cell in enumerate(rows[heading]):
        match = re.fullmatch(r'продажи\s+(\d{1,2})\s*[-–—]\s*(\d{1,2})', norm(cell))
        if match:
            a, b = map(int, match.groups())
            if not 1 <= a <= b <= 31:
                raise SheetParseError('invalid_layout_or_value')
            # Templates often use day 31 for the end of every month.
            periods.append((col, a, min(b, last)))
    cumulative = bool(periods) and all(a == 1 for _, a, _ in periods)
    if any(periods[i][2] >= periods[i+1][2] for i in range(len(periods)-1)):
        raise SheetParseError('invalid_layout_or_value')
    if not cumulative:
        expected = 1
        for _, a, b in periods:
            if a != expected:
                raise SheetParseError('invalid_layout_or_value')
            expected = b+1
    people = []
    seen = set()
    for row in rows[heading+1:end]:
        name = row[0].strip() if row else ''
        key = norm(name)
        if (not name or len(name)>80 or not any(c.isalpha() for c in name) or
            re.search(r'^(остальн|итого|всего|план|средний|официант)', key)):
            continue
        if key in seen:
            raise SheetParseError('duplicate_employee_names')
        seen.add(key)
        values = []
        gap = False
        for col, _, b in periods:
            cutoff = date(year, mon, b)
            if cutoff > today:
                break
            raw = row[col].strip() if col < len(row) else ''
            if not raw:
                gap = True
                continue
            if gap and not cumulative:
                raise SheetParseError('invalid_layout_or_value')
            values.append((cutoff, amount(raw)))
        if values:
            total = values[-1][1] if cumulative else sum(Decimal(str(v)) for _, v in values)
            try:
                total = validate_values({'turnover': total}, ('turnover',))['turnover']
            except ValueError:
                raise SheetParseError('invalid_total') from None
            people.append({'name': name, 'totals': {'turnover': total}, 'cutoff': values[-1][0].isoformat()})
    if len(people) > 30:
        raise SheetParseError('invalid_layout_or_value')
    return people


async def read(sheet_id, name, today):
    month = sheet_month(name)
    if not month:
        raise SheetParseError('invalid_layout_or_value')
    text = await fetch(sheet_id, sheet=name)
    if not text.strip() or text.lstrip().startswith('<'):
        raise ValueError(ACCESS_ERROR)
    rows=list(csv.reader(io.StringIO(text)))
    for row in rows:
        if row and norm(row[0])=='официант' and len(row)>1:
            label=norm(row[1])
            if label and label not in ('часы план','план часы','план открытки') and not label.startswith('продажи '):
                raise SheetParseError('categories_not_configured')
    return parse_rows(rows, month, today)


def parse_configured(rows, cfg, today):
    """Read named blocks against approved monthly definitions, never by position."""
    import categories
    if not rows or len(rows)>300 or any(len(r)>100 for r in rows):raise SheetParseError('invalid_layout')
    month=cfg['month'];year,mon=map(int,month.split('-'));last=calendar.monthrange(year,mon)[1]
    headings=[i for i,r in enumerate(rows) if r and norm(r[0])=='официант']
    people={};matched=set()
    def label_key(value):
        return re.sub(r'^план\s+', '',norm(value)).strip()
    for h, stop in zip(headings,headings[1:]+[len(rows)]):
        header=rows[h];block=rows[h:stop]
        labels={label_key(c) for c in header[1:] if c.strip()}
        if any(re.match(r'^план\s+оборот\s*/?\s*час\s*(?:=|$)',norm(' '.join(row))) for row in block):labels|={'товарооборот','оборот'}
        found=[d for d in cfg['definitions'] if label_key(d['name']) in labels]
        if not found:continue
        if len(found)!=1:raise SheetParseError('ambiguous_block')
        definition=found[0];cid=definition['id']
        if cid in matched:raise SheetParseError('duplicate_block')
        matched.add(cid)
        periods=[]
        for col,c in enumerate(header):
            m=re.fullmatch(r'.*?\b(\d{1,2})\s*[-–—]\s*(\d{1,2})',norm(c))
            if m and any(w in norm(c) for w in ('продажи',label_key(definition['name']))):
                a,b=map(int,m.groups())
                if not 1<=a<=b<=31:raise SheetParseError('invalid_period')
                periods.append((col,a,min(b,last)))
        if not periods:raise SheetParseError('missing_periods')
        cumulative=all(a==1 for _,a,_ in periods)
        if any(periods[i][2]>=periods[i+1][2] for i in range(len(periods)-1)):raise SheetParseError('invalid_periods')
        if not cumulative:
            expected=1
            for _,a,b in periods:
                if a!=expected:raise SheetParseError('overlapping_or_missing_period')
                expected=b+1
        seen=set()
        for row in rows[h+1:stop]:
            name=row[0].strip() if row else '';key=categories.key(name)
            if not name or len(name)>80 or not any(c.isalpha() for c in name) or re.search(r'^(остальн|итого|всего|план|средний|официант)',key):continue
            if key in seen:raise SheetParseError('duplicate_employee_names')
            seen.add(key);read=[];gap=False
            for col,_,b in periods:
                day=date(year,mon,b)
                if day>today:break
                raw=row[col].strip() if col<len(row) else ''
                if not raw:gap=True;continue
                if gap and not cumulative:raise SheetParseError('missing_period')
                v=amount(raw)
                try:v=categories.number(v,count=definition['unit']=='count',zero=True)
                except ValueError:raise SheetParseError('invalid_unit_value') from None
                read.append((day,v))
            if not read:continue
            total=read[-1][1] if cumulative else sum(Decimal(str(v)) for _,v in read)
            total=categories.number(total,count=definition['unit']=='count',zero=True)
            metric={'value':total,'cutoff':read[-1][0].isoformat()}
            # The column immediately preceding the first actual is the personal
            # goal only if it is the category/plan column; never worked hours.
            goal_col=periods[0][0]-1
            if goal_col>=1 and goal_col<len(row) and row[goal_col].strip() and (
                'план' in norm(header[goal_col]) or label_key(header[goal_col])==label_key(definition['name'])):
                target=categories.number(amount(row[goal_col]),count=definition['unit']=='count',zero=True)
                if target>0:metric['target']=target
            person=people.setdefault(key,{'name':name,'category_values':{}})
            person['category_values'][cid]=metric
    if not people:raise SheetParseError('no_matching_categories')
    if len(people)>30:raise SheetParseError('too_many_people')
    return list(people.values())


def discover_rows(rows, cfg, today):
    """Discover category names from labeled blocks; never from employee/value cells."""
    import categories
    if not rows or len(rows)>300 or any(len(r)>100 for r in rows):raise SheetParseError('invalid_layout')
    headings=[i for i,row in enumerate(rows) if row and norm(row[0])=='официант']
    detected=[]
    for h,stop in zip(headings,headings[1:]+[len(rows)]):
        header=rows[h];block=rows[h:stop]
        first=next((i for i,c in enumerate(header) if re.search(r'\b\d{1,2}\s*[-–—]\s*\d{1,2}\s*$',norm(c))),None)
        if first is None:continue
        names=[]
        for cell in header[1:first]:
            text=norm(cell)
            if not text or text in ('часы план','план часы','часы','план') or text.startswith('план продаж'):continue
            name=re.sub(r'^план\s+', '',cell.strip(),flags=re.I)
            unit=None
            if re.search(r'(?:₽|руб(?:\.|лей)?|\bшт\.?)',name,re.I):
                unit='count' if re.search(r'\bшт',name,re.I) else 'amount'
                name=re.sub(r'\s*[,·(]?\s*(?:₽|руб(?:\.|лей)?|шт\.?)\s*\)?\s*$','',name,flags=re.I).strip()
            names.append({'name':name,'unit':unit})
        if not names and any(re.match(r'^план\s+оборот\s*/?\s*час\s*(?:=|$)',norm(' '.join(r))) for r in block):
            names=[{'name':'Товарооборот','unit':'amount'}]
        if len(names)!=1:raise SheetParseError('ambiguous_category_heading')
        detected.append(names[0])
    if not detected:raise SheetParseError('no_matching_categories')
    observed=categories.discover(cfg,detected)
    # Strip only an explicit unit suffix in headings for deterministic matching.
    normalized=[list(row) for row in rows]
    for row in normalized:
        if row and norm(row[0])=='официант':
            for i in range(1,len(row)):
                row[i]=re.sub(r'\s*[,·(]?\s*(?:₽|руб(?:\.|лей)?|шт\.?)\s*\)?\s*$','',row[i],flags=re.I).strip()
    return parse_configured(normalized,observed,today),observed
