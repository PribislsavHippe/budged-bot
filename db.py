"""Доступ к Supabase. Единственный источник правды о деньгах — таблица entries.

Баланс НИКОГДА не хранится отдельно: он всегда пересчитывается как сумма
signed_amount по журналу. Это гарантирует, что баланс и история не разойдутся.
"""
import os

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"],
)

CASH = "cash"
CARD = "card"
ACCOUNTS = (CASH, CARD)

ACCOUNT_LABELS = {CASH: "Наличные", CARD: "Карта"}


# ─── users ───────────────────────────────────────────────────────────────────

async def get_or_create_user(user_id: int) -> dict:
    """Профиль по Telegram id. Имя и @username не храним — см. schema.sql."""
    res = supabase.table("users").select("*").eq("id", user_id).execute()
    if res.data:
        return res.data[0]
    res = supabase.table("users").insert({"id": user_id}).execute()
    return res.data[0]


async def set_onboarded(user_id: int) -> None:
    supabase.table("users").update({"onboarded": True}).eq("id", user_id).execute()


async def clear_entries(user_id: int) -> None:
    """Очистить журнал, оставив профиль (/reset)."""
    supabase.table("entries").delete().eq("user_id", user_id).execute()


async def delete_user(user_id: int) -> None:
    """Стереть человека целиком: журнал, смены, профиль с токенами (/delete).

    Удаляем явно, а не полагаясь на ON DELETE CASCADE, чтобы результат не
    зависел от того, как заведены внешние ключи в конкретной базе.
    """
    supabase.table("entries").delete().eq("user_id", user_id).execute()
    supabase.table("shifts").delete().eq("user_id", user_id).execute()
    supabase.table("users").delete().eq("id", user_id).execute()


# ─── entries ─────────────────────────────────────────────────────────────────

async def add_entry(
    user_id: int,
    kind: str,
    account: str,
    signed_amount: float,
    category: str = "Прочее",
    note: str | None = None,
    order_amount: float | None = None,
    tip_percent: float | None = None,
) -> dict:
    assert kind in ("income", "expense", "adjustment"), kind
    assert account in ACCOUNTS, account
    data = {
        "user_id": user_id,
        "kind": kind,
        "account": account,
        "signed_amount": round(signed_amount, 2),
        "category": category,
        "note": note,
    }
    if order_amount is not None:
        data["order_amount"] = order_amount
    if tip_percent is not None:
        data["tip_percent"] = tip_percent
    res = supabase.table("entries").insert(data).execute()
    return res.data[0]


async def get_entry(entry_id: int, user_id: int) -> dict | None:
    res = supabase.table("entries").select("*") \
        .eq("id", entry_id).eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


async def update_entry_account(entry_id: int, user_id: int, account: str) -> dict | None:
    assert account in ACCOUNTS, account
    res = supabase.table("entries").update({"account": account}) \
        .eq("id", entry_id).eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


async def update_entry_amount(entry_id: int, user_id: int, signed_amount: float) -> dict | None:
    res = supabase.table("entries").update({"signed_amount": round(signed_amount, 2)}) \
        .eq("id", entry_id).eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


async def delete_entry(entry_id: int, user_id: int) -> bool:
    """Возвращает True, если запись существовала и была удалена."""
    res = supabase.table("entries").delete() \
        .eq("id", entry_id).eq("user_id", user_id).execute()
    return bool(res.data)


async def get_recent_entries(user_id: int, limit: int = 15) -> list[dict]:
    res = supabase.table("entries").select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True).order("id", desc=True) \
        .limit(limit).execute()
    return res.data


async def get_entries_since(user_id: int, since_iso: str) -> list[dict]:
    res = supabase.table("entries").select("*") \
        .eq("user_id", user_id) \
        .gte("created_at", since_iso) \
        .order("created_at", desc=True).execute()
    return res.data


async def get_all_entries(user_id: int) -> list[dict]:
    res = supabase.table("entries").select("*") \
        .eq("user_id", user_id).order("created_at").execute()
    return res.data


# ─── план смены ──────────────────────────────────────────────────────────────

async def get_shift_goal(user_id: int) -> float | None:
    res = supabase.table("users").select("shift_goal").eq("id", user_id).execute()
    if res.data and res.data[0].get("shift_goal") is not None:
        return float(res.data[0]["shift_goal"])
    return None


async def set_shift_goal(user_id: int, goal: float | None) -> None:
    supabase.table("users").update({"shift_goal": goal}).eq("id", user_id).execute()


async def get_onboarded_user_ids() -> list[int]:
    res = supabase.table("users").select("id").eq("onboarded", True).execute()
    return [row["id"] for row in res.data]


# ─── агрегаты для админки ────────────────────────────────────────────────────
# Только счётчики и идентификаторы. Сумм конкретного человека эти запросы не
# возвращают и возвращать не должны — на этом держится раздел «Приватность»
# в README, ради которого из базы убирали имена.

async def count_users() -> int:
    res = supabase.table("users").select("id", count="exact").execute()
    return res.count or 0


async def count_onboarded_users() -> int:
    res = supabase.table("users").select("id", count="exact").eq("onboarded", True).execute()
    return res.count or 0


async def count_users_since(since_iso: str) -> int:
    res = supabase.table("users").select("id", count="exact") \
        .gte("created_at", since_iso).execute()
    return res.count or 0


async def count_entries_since(since_iso: str) -> int:
    res = supabase.table("entries").select("id", count="exact") \
        .gte("created_at", since_iso).execute()
    return res.count or 0


async def active_user_ids_since(since_iso: str) -> set[int]:
    """Кто вообще что-то записал за период — только id, без сумм."""
    res = supabase.table("entries").select("user_id").gte("created_at", since_iso).execute()
    return {row["user_id"] for row in res.data}


async def count_shifts_on(date_iso: str) -> int:
    res = supabase.table("shifts").select("id", count="exact") \
        .eq("shift_date", date_iso).execute()
    return res.count or 0


# ─── расписание смен ─────────────────────────────────────────────────────────

async def add_shifts(user_id: int, dates: list[str]) -> None:
    """Ставит смены на даты (ISO YYYY-MM-DD). Дубли игнорируются."""
    if not dates:
        return
    rows = [{"user_id": user_id, "shift_date": d} for d in dates]
    supabase.table("shifts").upsert(
        rows, on_conflict="user_id,shift_date", ignore_duplicates=True
    ).execute()


async def get_shift_dates(user_id: int, since: str | None = None, until: str | None = None) -> list[str]:
    q = supabase.table("shifts").select("shift_date").eq("user_id", user_id)
    if since:
        q = q.gte("shift_date", since)
    if until:
        q = q.lte("shift_date", until)
    res = q.order("shift_date").execute()
    return [row["shift_date"] for row in res.data]


async def has_shift_on(user_id: int, date_iso: str) -> bool:
    res = supabase.table("shifts").select("id") \
        .eq("user_id", user_id).eq("shift_date", date_iso).limit(1).execute()
    return bool(res.data)


async def delete_shift(user_id: int, date_iso: str) -> bool:
    res = supabase.table("shifts").delete() \
        .eq("user_id", user_id).eq("shift_date", date_iso).execute()
    return bool(res.data)


async def get_user_ids_with_shift_on(date_iso: str) -> list[int]:
    res = supabase.table("shifts").select("user_id").eq("shift_date", date_iso).execute()
    return [row["user_id"] for row in res.data]


# ─── Google Календарь (OAuth-токены) ─────────────────────────────────────────

async def save_google_token(user_id: int, access_token: str,
                            refresh_token: str | None, expiry_iso: str | None) -> None:
    data = {"google_access_token": access_token, "google_token_expiry": expiry_iso}
    if refresh_token:  # при refresh Google не возвращает refresh_token заново
        data["google_refresh_token"] = refresh_token
    supabase.table("users").update(data).eq("id", user_id).execute()


async def get_google_token(user_id: int) -> dict | None:
    res = supabase.table("users").select(
        "google_access_token, google_refresh_token, google_token_expiry"
    ).eq("id", user_id).execute()
    return res.data[0] if res.data else None


async def clear_google_token(user_id: int) -> None:
    supabase.table("users").update({
        "google_access_token": None, "google_refresh_token": None, "google_token_expiry": None,
    }).eq("id", user_id).execute()
