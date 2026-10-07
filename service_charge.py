"""Personal service charge accrued now and paid later with salary."""

import re
from decimal import Decimal, InvalidOperation

from workday import entry_op_date


_PREFIX = re.compile(r"^\s*(?:сервисный\s+сбор|сс)\b", re.IGNORECASE)
_RECORD = re.compile(
    r"^\s*(?:сервисный\s+сбор|сс)\s*[:—-]?\s*(\d[\d ]*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:лей|ля|\.)?)?\s*$",
    re.IGNORECASE,
)


def parse(text: str) -> float | None:
    """Return amount, None for unrelated text, or ValueError for malformed intent."""
    if not _PREFIX.match(text):
        return None
    match = _RECORD.fullmatch(text)
    if not match:
        raise ValueError("Напиши сумму так: сс 2000")
    try:
        amount = Decimal(match.group(1).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise ValueError("Проверь сумму сервисного сбора") from None
    if not 0 < amount <= 10_000_000:
        raise ValueError("Сумма сервисного сбора должна быть больше нуля и не больше 10 млн ₽")
    return float(amount)


def summarize(entries: list[dict], month: str) -> dict:
    records = []
    for entry in entries:
        if entry.get("kind") != "accrual" or entry.get("category") != "Сервисный сбор":
            continue
        day = entry.get("work_date") or entry_op_date(entry["created_at"]).isoformat()
        if day[:7] == month:
            records.append({"date": day, "amount": float(entry["signed_amount"])})
    return {"total": round(sum(item["amount"] for item in records), 2), "count": len(records)}
