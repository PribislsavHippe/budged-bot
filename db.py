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

async def get_or_create_user(user_id: int, username: str | None, first_name: str | None) -> dict:
    res = supabase.table("users").select("*").eq("id", user_id).execute()
    if res.data:
        return res.data[0]
    res = supabase.table("users").insert(
        {"id": user_id, "username": username, "first_name": first_name}
    ).execute()
    return res.data[0]


async def set_onboarded(user_id: int) -> None:
    supabase.table("users").update({"onboarded": True}).eq("id", user_id).execute()


# ─── entries ─────────────────────────────────────────────────────────────────

async def add_entry(
    user_id: int,
    kind: str,
    account: str,
    signed_amount: float,
    category: str = "Прочее",
    note: str | None = None,
) -> dict:
    assert kind in ("income", "expense", "adjustment"), kind
    assert account in ACCOUNTS, account
    res = supabase.table("entries").insert({
        "user_id": user_id,
        "kind": kind,
        "account": account,
        "signed_amount": round(signed_amount, 2),
        "category": category,
        "note": note,
    }).execute()
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


async def delete_entry(entry_id: int, user_id: int) -> bool:
    """Возвращает True, если запись существовала и была удалена."""
    res = supabase.table("entries").delete() \
        .eq("id", entry_id).eq("user_id", user_id).execute()
    return bool(res.data)


async def get_balances(user_id: int) -> dict:
    """{'cash': float, 'card': float, 'total': float} — всегда из журнала."""
    res = supabase.table("entries").select("account, signed_amount") \
        .eq("user_id", user_id).execute()
    balances = {CASH: 0.0, CARD: 0.0}
    for row in res.data:
        balances[row["account"]] += float(row["signed_amount"])
    balances["total"] = balances[CASH] + balances[CARD]
    return {k: round(v, 2) for k, v in balances.items()}


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
