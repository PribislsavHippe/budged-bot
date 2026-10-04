"""A read-only iCalendar feed containing only planned shifts."""
import hashlib
import hmac
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo('Europe/Moscow')


def feed_token(bot_token: str, feed_id: str, salt: str) -> str:
    message = f'iphone-calendar:{feed_id}:{salt}'.encode()
    return hmac.new(bot_token.encode(), message, hashlib.sha256).hexdigest()


def _stamp(value: str | None) -> str:
    if value:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _local_utc(day: date, clock: str, next_day: bool = False) -> str:
    hour, minute = map(int, clock[:5].split(':'))
    moment = datetime.combine(day + timedelta(days=next_day), time(hour, minute), MOSCOW)
    return moment.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _fold(line: str) -> list[str]:
    """RFC 5545 content lines have a 75-octet limit, including the fold space."""
    result = []
    part = ''
    octets = 0
    for character in line:
        size = len(character.encode('utf-8'))
        if octets + size > 75:
            result.append(part)
            part, octets = ' ', 1
        part += character
        octets += size
    result.append(part)
    return result


def render_shifts(rows: list[dict], user_id: int, bot_token: str) -> bytes:
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0',
        'PRODID:-//Budget Bot//Shift Calendar//RU',
        'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
        'X-WR-CALNAME:Мои смены', 'X-WR-TIMEZONE:Europe/Moscow',
    ]
    for row in rows:
        day = date.fromisoformat(row['shift_date'])
        date_text = day.strftime('%Y%m%d')
        uid = hmac.new(bot_token.encode(), f'shift:{user_id}:{day.isoformat()}'.encode(),
                       hashlib.sha256).hexdigest()[:32]
        modified = _stamp(row.get('calendar_updated_at') or row.get('created_at'))
        lines.extend(['BEGIN:VEVENT', f'UID:{uid}@budgetbot',
                      f'DTSTAMP:{modified}', f'LAST-MODIFIED:{modified}',
                      'SUMMARY:Смена'])
        start, end = row.get('starts_at'), row.get('ends_at')
        if start and end:
            overnight = end[:5] <= start[:5]
            lines.extend([f'DTSTART:{_local_utc(day, start)}',
                          f'DTEND:{_local_utc(day, end, overnight)}'])
        else:
            lines.extend([f'DTSTART;VALUE=DATE:{date_text}',
                          f'DTEND;VALUE=DATE:{(day + timedelta(days=1)):%Y%m%d}'])
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return ('\r\n'.join(part for line in lines for part in _fold(line)) + '\r\n').encode('utf-8')
