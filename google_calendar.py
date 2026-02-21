import os
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from db import get_google_token, save_google_token

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def get_auth_url(user_id: int) -> str:
    """Генерирует URL для авторизации пользователя в Google"""
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=str(user_id),
        prompt="consent"
    )
    return auth_url


async def exchange_code(user_id: int, code: str):
    """Обменивает код авторизации на токен и сохраняет в БД"""
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    flow.fetch_token(code=code)

    creds = flow.credentials
    await save_google_token(
        user_id=user_id,
        access_token=creds.token,
        refresh_token=creds.refresh_token,
        expiry=creds.expiry
    )
    return creds


async def get_credentials(user_id: int) -> Credentials | None:
    """Получает актуальные credentials для пользователя"""
    token_data = await get_google_token(user_id)
    if not token_data:
        return None

    creds = Credentials(
        token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES
    )

    # Обновляем токен если истёк
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        await save_google_token(
            user_id=user_id,
            access_token=creds.token,
            refresh_token=creds.refresh_token,
            expiry=creds.expiry
        )

    return creds


async def create_payment_event(user_id: int, payment_name: str, amount: float, date: datetime) -> bool:
    """Создаёт событие в Google Calendar для платежа"""
    creds = await get_credentials(user_id)
    if not creds:
        return False

    service = build("calendar", "v3", credentials=creds)

    event = {
        "summary": f"{payment_name} — {amount:,.0f} ₽",
        "description": f"Обязательный платёж: {payment_name}\nСумма: {amount:,.2f} ₽\n\nДобавлено через Budget Bot",
        "start": {"date": date.strftime("%Y-%m-%d")},
        "end": {"date": date.strftime("%Y-%m-%d")},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60 * 24 * 2},  # за 2 дня
                {"method": "popup", "minutes": 60 * 9},        # за 9 часов
            ]
        },
        "colorId": "11"  # красный
    }

    service.events().insert(calendarId="primary", body=event).execute()
    return True


async def create_income_reminder(user_id: int, date: datetime) -> bool:
    """Создаёт напоминание о дне зарплаты"""
    creds = await get_credentials(user_id)
    if not creds:
        return False

    service = build("calendar", "v3", credentials=creds)

    event = {
        "summary": "День зарплаты — внеси доход в Budget Bot",
        "description": "Не забудь зафиксировать доход в Budget Bot!",
        "start": {"date": date.strftime("%Y-%m-%d")},
        "end": {"date": date.strftime("%Y-%m-%d")},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60 * 9},  # утром
            ]
        },
        "colorId": "10"  # зелёный
    }

    service.events().insert(calendarId="primary", body=event).execute()
    return True


SALARY_EVENT_SUMMARY = "День зарплаты — внеси доход в Budget Bot"


def _payment_event_summary(name: str, amount: float) -> str:
    return f"{name} — {amount:,.0f} ₽"


async def list_calendar_events(user_id: int, time_min: datetime, time_max: datetime) -> list[dict]:
    """Список событий в календаре за период. Каждый элемент: {"date": "YYYY-MM-DD", "summary": "..."}."""
    creds = await get_credentials(user_id)
    if not creds:
        return []

    service = build("calendar", "v3", credentials=creds)
    try:
        if time_min.tzinfo is None:
            time_min = time_min.replace(tzinfo=timezone.utc)
        if time_max.tzinfo is None:
            time_max = time_max.replace(tzinfo=timezone.utc)
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min.isoformat().replace("+00:00", "Z"),
                timeMax=time_max.isoformat().replace("+00:00", "Z"),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception:
        return []

    out = []
    for item in result.get("items", []):
        start = item.get("start", {})
        date_str = start.get("date") or (start.get("dateTime", "")[:10] if start.get("dateTime") else None)
        if date_str:
            out.append({"date": date_str, "summary": item.get("summary", "").strip()})
    return out


async def ensure_calendar_events(user_id: int, payments: list, salary_days: list, days_ahead: int = 60) -> dict:
    """
    Проверяет календарь за ближайшие days_ahead дней: если нет нужных событий (платежи, день зарплаты) — создаёт.
    Возвращает {"created": N, "checked": M}.
    """
    creds = await get_credentials(user_id)
    if not creds:
        return {"created": 0, "checked": 0}

    now = datetime.now(timezone.utc)
    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
    time_max = time_min + timedelta(days=days_ahead)

    existing = await list_calendar_events(user_id, time_min, time_max)
    existing_set = {(e["date"], e["summary"]) for e in existing}

    created = 0
    # Ожидаемые события: для каждой даты в диапазоне
    for d in range(days_ahead):
        cur = time_min + timedelta(days=d)
        date_str = cur.strftime("%Y-%m-%d")
        day = cur.day

        # День зарплаты
        if salary_days and day in salary_days:
            key = (date_str, SALARY_EVENT_SUMMARY)
            if key not in existing_set:
                if await create_income_reminder(user_id, cur):
                    existing_set.add(key)
                    created += 1

        # Платежи на этот день месяца
        for p in payments or []:
            if p.get("day_of_month") != day:
                continue
            summary = _payment_event_summary(p["name"], p["amount"])
            key = (date_str, summary)
            if key not in existing_set:
                if await create_payment_event(user_id, p["name"], p["amount"], cur):
                    existing_set.add(key)
                    created += 1

    return {"created": created, "checked": days_ahead}
