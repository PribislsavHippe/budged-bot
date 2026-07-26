"""Операционный день: смена принадлежит тому дню, в который началась.

Официант закрывает смену после полуночи. Чай, записанный в 00:30, относится
к вчерашней смене, а не к новому дню — иначе одна смена разваливается на две:
«за сегодня» обнуляется посреди работы, а в статистике смен вдвое больше,
чем на самом деле, и средний чай за смену занижен.

Поэтому сутки считаем не с полуночи, а с DAY_START_HOUR утра по Москве:
всё, что записано раньше этого часа, уходит в предыдущий день.
"""
import os
from datetime import date, datetime, time, timedelta, timezone

MSK = timezone(timedelta(hours=3))

# Час по МСК, с которого начинается новый рабочий день.
DAY_START_HOUR = int(os.getenv("DAY_START_HOUR", "6"))


def now_msk() -> datetime:
    return datetime.now(MSK)


def op_date(moment: datetime) -> date:
    """Операционная дата момента. До DAY_START_HOUR — это ещё вчерашний день."""
    return (moment.astimezone(MSK) - timedelta(hours=DAY_START_HOUR)).date()


def op_today(now: datetime | None = None) -> date:
    """Какой сейчас операционный день."""
    return op_date(now or now_msk())


def op_day_start(day: date) -> datetime:
    """Момент начала операционного дня (МСК)."""
    return datetime.combine(day, time(DAY_START_HOUR), tzinfo=MSK)


def op_day_start_utc_iso(day: date) -> str:
    """Начало операционного дня в UTC ISO — в таком виде хранится created_at."""
    return op_day_start(day).astimezone(timezone.utc).isoformat()


def entry_op_date(created_at: str) -> date:
    """Операционная дата записи по её created_at из Supabase."""
    return op_date(datetime.fromisoformat(created_at.replace("Z", "+00:00")))
