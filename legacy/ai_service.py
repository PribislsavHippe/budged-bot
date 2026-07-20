"""
ai_service.py — Groq для СОВЕТОВ и ЧАТА.

Принципы разделения:
- Groq (этот файл): чат с пользователем, короткие советы, парсинг транзакций.
  Groq НЕ делает анализ денежных потоков — это задача Gemini (weekly_advice.py).
- Gemini (weekly_advice.py): глубокий финансовый анализ, отчёты, прогнозы.
"""

import os
import json
import logging
from datetime import datetime, timezone, date, timedelta
from groq import AsyncGroq

_client: AsyncGroq | None = None
GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_WHISPER_MODEL = "whisper-large-v3"
GROQ_VISION_MODEL  = "meta-llama/llama-4-scout-17b-16e-instruct"


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY не задан в окружении")
        _client = AsyncGroq(api_key=api_key)
    return _client


GROQ_NO_CALC_SYSTEM = (
    "Ты финансовый помощник. ВАЖНО: ты НЕ делаешь математических вычислений — "
    "никаких сложений, вычитаний, умножений, процентов, прогнозов в цифрах. "
    "Все расчёты уже сделаны Python-кодом и переданы тебе в виде готовых чисел. "
    "Твоя задача: интерпретировать, объяснять, советовать — словами. "
    "Если тебя просят посчитать — скажи, что расчёты делает система, и дай совет по ситуации."
)


async def _generate(prompt: str, system: str = None, max_tokens: int = 800) -> str:
    client = _get_client()
    messages = []
    # Базовый запрет вычислений — всегда, если не переопределён
    base_system = system if system else "Финансовый помощник. Кратко, на «ты». Без эмодзи."
    full_system = GROQ_NO_CALC_SYSTEM + "\n\n" + base_system
    messages.append({"role": "system", "content": full_system})
    messages.append({"role": "user", "content": prompt})
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


EXPENSE_CATEGORIES = [
    "Еда", "Кафе и рестораны", "Транспорт", "Жильё",
    "Развлечения", "Здоровье", "Одежда", "Связь",
    "Образование", "Кредиты", "Обязательные", "Прочее"
]

INCOME_CATEGORIES = [
    "Зарплата", "Оплата за неделю", "Аванс", "Частичная оплата",
    "Фриланс", "Подработка", "Подарок", "Инвестиции", "Прочее"
]


def build_datetime_context(now_dt=None) -> str:
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    try:
        import pytz
        tz = pytz.timezone("Europe/Moscow")
        local = now_dt.astimezone(tz)
    except Exception:
        local = now_dt
    weekdays_ru = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    wd = weekdays_ru[local.weekday()]
    return f"Сегодня {local.strftime('%d.%m.%Y')} ({wd}), время {local.strftime('%H:%M')} МСК."


# ─── ПАРСИНГ ТРАНЗАКЦИЙ ───────────────────────────────────────────────────────

async def parse_transaction(text: str) -> dict | None:
    now_ctx = build_datetime_context()
    prompt = f"""Пользователь написал: "{text}"

{now_ctx}

Это ФАКТ уже совершённой траты или полученного дохода?

НЕ транзакция — верни is_transaction: false:
- Вопросы (есть "?"), рассуждения, советы
- Упоминание будущих событий, планы
- Числа-даты: "9 и 11 числа" — это даты, не суммы
- Просьбы об анализе

Транзакция — только факт: "потратил 500", "купил кофе 180", "зарплата 50000"

Если транзакция: {{"is_transaction": true, "type": "expense" или "income", "amount": число, "category": из списка, "description": краткое}}
Если нет: {{"is_transaction": false}}

Категории расходов: {", ".join(EXPENSE_CATEGORIES)}
Категории доходов: {", ".join(INCOME_CATEGORIES)}

Только JSON."""

    try:
        raw = await _generate(prompt)
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return result if result.get("is_transaction") else None
    except Exception as e:
        logging.error(f"parse_transaction error: {e}")
        return None


async def parse_bulk_transactions(text: str) -> list[dict]:
    now_ctx = build_datetime_context()
    prompt = (
        f'Пользователь написал: "{text}"\n\n'
        f"{now_ctx}\n\n"
        "Найди ВСЕ транзакции (траты и доходы). Для каждой: "
        '{"type": "expense"/"income", "amount": число, "category": из списка, "description": краткое}\n\n'
        f"Категории расходов: {', '.join(EXPENSE_CATEGORIES)}\n"
        f"Категории доходов: {', '.join(INCOME_CATEGORIES)}\n\n"
        "Верни JSON массив. Если нет транзакций (вопрос, просьба об анализе) — верни: []\n"
        "Только JSON."
    )
    try:
        raw = await _generate(prompt)
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return [r for r in result if r.get("amount") and r.get("type") in ("expense", "income")]
        return []
    except Exception as e:
        logging.error(f"parse_bulk_transactions error: {e}")
        return []


async def parse_onboarding_payments(text: str) -> list[dict]:
    prompt = f"""Пользователь описывает регулярные платежи: "{text}"

Извлеки все. Для каждого:
- name: название
- amount: сумма числом
- day: день месяца (если указан, иначе 1)

Верни JSON массив: [{{"name": "...", "amount": число, "day": число}}]
Если ничего — верни: []
Только JSON."""

    try:
        raw = await _generate(prompt)
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception as e:
        logging.error(f"parse_onboarding_payments error: {e}")
        return []


# ─── ЧАТ (ТОЛЬКО СОВЕТЫ, НЕ АНАЛИЗ) ─────────────────────────────────────────

async def chat_with_ai(
    user_message: str,
    stats: dict,
    payments: list,
    context_extra: str = "",
    budgets: list = None,
    planned_income: list = None,
) -> str:
    """
    Groq отвечает на вопросы пользователя в чате.
    Даёт СОВЕТЫ и ОТВЕТЫ на вопросы.
    НЕ делает глубокий анализ денежных потоков — для этого есть Gemini.
    """
    by_category = stats.get("by_category", {})
    by_income_category = stats.get("by_income_category", {})
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)

    date_ctx = build_datetime_context()
    today = date.today()

    payments_text = ""
    if payments:
        future = sorted([p for p in payments if p["day_of_month"] >= today.day], key=lambda x: x["day_of_month"])
        past   = sorted([p for p in payments if p["day_of_month"] <  today.day], key=lambda x: x["day_of_month"])
        if future:
            lines = [f"  {p['day_of_month']}-е: {p['name']} {p['amount']:,.0f} ₽" for p in future]
            payments_text += "\nПлатежи впереди:\n" + "\n".join(lines)
        if past:
            lines = [f"  {p['day_of_month']}-е: {p['name']} {p['amount']:,.0f} ₽" for p in past]
            payments_text += "\nПлатежи прошли:\n" + "\n".join(lines)

    planned_text = ""
    if planned_income:
        lines = []
        for p in planned_income:
            d_str = p.get("expected_date", "")[:10]
            try:
                d = date.fromisoformat(d_str)
                rel = "впереди" if d >= today else "прошло"
            except Exception:
                rel = ""
            desc = f" ({p.get('description', '')})" if p.get("description") else ""
            lines.append(f"  {d_str}: {p.get('amount', 0):,.0f} ₽{desc} [{rel}]")
        planned_text = "\nПланируемые записи:\n" + "\n".join(lines)

    budgets_text = ""
    if budgets:
        budgets_text = "\nЛимиты:\n" + "\n".join(
            [f"  {b['category']}: {b['limit_amount']:,.0f} ₽/мес" for b in budgets]
        )

    cat_list = "\n".join([f"  {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])
    inc_list = "\n".join([f"  {cat}: {amt:,.0f} ₽" for cat, amt in by_income_category.items()]) if by_income_category else "  нет данных"

    system = (
        "Ты персональный финансовый помощник — умный и конкретный. "
        "Отвечаешь на вопросы, даёшь практические СОВЕТЫ. "
        "Обращайся на «ты». Без эмодзи. Кратко, с цифрами. "
        "НЕ делай глубокий анализ денежных потоков — для этого есть отдельная функция. "
        "Просто отвечай на вопрос конкретно."
    )

    user_prompt = (
        f"{date_ctx}\n{context_extra}\n{payments_text}\n{planned_text}\n{budgets_text}\n\n"
        f"Финансы за последние 30 дней:\n"
        f"Доходы: {income:,.0f} ₽\n"
        f"Расходы: {expenses:,.0f} ₽\n"
        f"Баланс: {balance:,.0f} ₽\n"
        f"Расходы по категориям:\n{cat_list or '  нет данных'}\n"
        f"Доходы по категориям:\n{inc_list}\n\n"
        f"Вопрос: {user_message}"
    )

    try:
        return await _generate(user_prompt, system=system, max_tokens=600)
    except Exception as e:
        logging.error(f"chat_with_ai error: {e}")
        return f"Что-то сломалось: {str(e)}"


# ─── КОРОТКИЙ СОВЕТ (НЕ АНАЛИЗ) ──────────────────────────────────────────────

async def get_ai_advice(stats: dict, user_name: str = "друг") -> str:
    """
    Короткий совет по текущей ситуации — НЕ полный анализ.
    Для полного анализа используй weekly_advice.py (Gemini).
    """
    by_category = stats.get("by_category", {})
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)

    if not by_category and income == 0:
        return "Данных пока нет. Вноси расходы несколько дней — тогда дам конкретный совет."

    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    prompt = (
        f"Доходы: {income:,.0f} ₽, Расходы: {expenses:,.0f} ₽, Баланс: {balance:,.0f} ₽\n"
        f"По категориям:\n{cat_list or 'нет данных'}\n\n"
        "Дай ОДИН конкретный совет — что улучшить прямо сейчас. С цифрами. 3-4 предложения."
    )

    try:
        return await _generate(prompt, system="Финансовый советник. Коротко, на «ты», с цифрами. Без эмодзи.")
    except Exception as e:
        logging.error(f"get_ai_advice error: {e}")
        return "Не удалось получить совет."


async def generate_weekly_ai_report(stats: dict) -> str | None:
    """Короткий итог недели — 3 предложения."""
    if stats.get("transactions_count", 0) == 0:
        return None

    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)
    by_category = stats.get("by_category", {})
    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    prompt = (
        f"Неделя: доходы {income:,.0f} ₽, расходы {expenses:,.0f} ₽, баланс {balance:,.0f} ₽\n"
        f"Категории: {cat_list or 'нет'}\n\n"
        "Итог недели в 2-3 предложениях + один совет на следующую. Без анализа денежных потоков."
    )

    try:
        return await _generate(prompt, system="Финансовый советник. На «ты», коротко. Без эмодзи.")
    except Exception as e:
        logging.error(f"weekly report error: {e}")
        return None


async def get_smart_budget_advice(stats: dict, days_until_salary: int, mandatory_expenses: float, salary_day: int) -> str:
    balance = stats.get("balance", 0)
    by_category = stats.get("by_category", {})
    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])
    free = balance - mandatory_expenses
    daily = free / days_until_salary if days_until_salary > 0 else 0

    prompt = (
        f"Баланс: {balance:,.0f} ₽. До зарплаты ({salary_day}-е): {days_until_salary} дней.\n"
        f"Обяз. платежи: {mandatory_expenses:,.0f} ₽. Свободно: {free:,.0f} ₽. В день: {daily:,.0f} ₽.\n"
        f"Категории:\n{cat_list or 'нет данных'}\n\n"
        "Дай совет: как дотянуть до зарплаты. Конкретно, 3-4 предложения."
    )

    try:
        return await _generate(prompt, system="Советник. Коротко, с цифрами. Без эмодзи.")
    except Exception as e:
        logging.error(f"budget advice error: {e}")
        return None


async def evaluate_goal(
    stats: dict, payments: list, planned_income: list,
    target_amount: float, target_months: int, monthly_amount: float, salary_days: list,
) -> str:
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)
    payments_sum = sum(p.get("amount", 0) for p in payments) if payments else 0

    prompt = (
        f"Хочет накопить {target_amount:,.0f} ₽ за {target_months} мес. (откладывать {monthly_amount:,.0f} ₽/мес).\n"
        f"Доходы: {income:,.0f} ₽, расходы: {expenses:,.0f} ₽, баланс: {balance:,.0f} ₽.\n"
        f"Обязательные платежи: ~{payments_sum:,.0f} ₽/мес.\n\n"
        "Реалистична ли цель? 3 предложения."
    )

    try:
        return await _generate(prompt, system="Советник. Честно, с цифрами. Без эмодзи.")
    except Exception as e:
        logging.error(f"evaluate_goal error: {e}")
        return "Цель сохранена. Откладывай сразу в день зарплаты."


# ─── ГОЛОС И ФОТО ─────────────────────────────────────────────────────────────

async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    import io
    client = _get_client()
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        transcription = await client.audio.transcriptions.create(
            file=(filename, audio_file),
            model=GROQ_WHISPER_MODEL,
            language="ru",
            response_format="text",
        )
        return str(transcription).strip() if transcription else None
    except Exception as e:
        logging.error(f"transcribe_voice error: {e}")
        return None


async def parse_receipt_photo(image_base64: str) -> list[dict]:
    client = _get_client()
    try:
        cats = ", ".join(EXPENSE_CATEGORIES)
        response = await client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    {"type": "text", "text": (
                        "Это фото чека. Определи итоговую сумму, магазин и тип покупки.\n"
                        f'Верни JSON: [{{"type": "expense", "amount": число, "category": из списка, "description": "магазин"}}]\n'
                        f"Категории: {cats}\n"
                        "Если не разобрал — верни: []\nТолько JSON."
                    )}
                ]
            }],
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return [r for r in result if r.get("amount")] if isinstance(result, list) else []
    except Exception as e:
        logging.error(f"parse_receipt_photo error: {e}")
        return []


# ─── УМНЫЙ ДАШБОРД (КРАТКИЙ — GROQ) ──────────────────────────────────────────

async def get_smart_dashboard(stats: dict, payments: list, salary_days: list, planned: list = None) -> str | None:
    """
    Краткий дашборд от Groq — только ключевые цифры и один вывод.
    Для подробного анализа — /week (Gemini).
    """
    from calendar import monthrange
    today = date.today()
    days_in_month = monthrange(today.year, today.month)[1]

    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)
    by_category = stats.get("by_category", {})

    next_salary_day = None
    days_to_salary = None
    if salary_days:
        future = [d for d in sorted(salary_days) if d > today.day]
        next_salary_day = future[0] if future else sorted(salary_days)[0]
        days_to_salary = (
            (next_salary_day - today.day) if next_salary_day > today.day
            else (days_in_month - today.day + next_salary_day)
        )

    future_pmts = [p for p in (payments or []) if p.get("day_of_month", 0) > today.day]
    future_pmts_sum = sum(p["amount"] for p in future_pmts)
    planned_inc = sum(p["amount"] for p in (planned or []) if p.get("type") == "income")

    free = balance - future_pmts_sum + planned_inc
    daily = free / days_to_salary if days_to_salary and days_to_salary > 0 else 0

    cat_lines = "\n".join([f"  {cat}: {amt:,.0f} руб." for cat, amt in by_category.items()])
    date_ctx = build_datetime_context()

    prompt = (
        f"{date_ctx}\n"
        f"День {today.day} из {days_in_month}.\n"
        f"Доходы: {income:,.0f} руб. Расходы: {expenses:,.0f} руб. Баланс: {balance:,.0f} руб.\n"
        f"Свободных до зарплаты ({next_salary_day}-го, через {days_to_salary} дней): {free:,.0f} руб.\n"
        f"Дневной бюджет: {daily:,.0f} руб.\n"
        f"Категории:\n{cat_lines or '  нет данных'}\n\n"
        "Дай краткий совет — 3-4 предложения. Главный вывод + что следить. "
        "Для подробного анализа рекомендуй /week. Без эмодзи."
    )

    try:
        return await _generate(prompt, system="Финансовый помощник. Кратко, конкретно. Без эмодзи.")
    except Exception as e:
        logging.error(f"get_smart_dashboard error: {e}")
        return None


async def get_trends_analysis(current_stats: dict, prev_stats: dict) -> str:
    curr_cat = current_stats.get("by_category", {})
    prev_cat = prev_stats.get("by_category", {})
    all_cats = set(list(curr_cat.keys()) + list(prev_cat.keys()))

    lines = []
    for cat in sorted(all_cats, key=lambda c: curr_cat.get(c, 0), reverse=True):
        curr = curr_cat.get(cat, 0)
        prev = prev_cat.get(cat, 0)
        delta = curr - prev
        pct = abs(delta / prev * 100) if prev > 0 else 0
        arrow = "вверх" if delta > 0 else "вниз"
        if prev > 0:
            lines.append(f"{cat}: {curr:,.0f} руб. ({arrow} {abs(delta):,.0f} руб., {pct:.0f}%)")
        else:
            lines.append(f"{cat}: {curr:,.0f} руб. (новая)")

    prompt = (
        f"Изменения по категориям:\n{chr(10).join(lines) if lines else 'нет данных'}\n\n"
        f"Текущий: доходы {current_stats.get('income',0):,.0f} руб., расходы {current_stats.get('expenses',0):,.0f} руб.\n"
        f"Прошлый: доходы {prev_stats.get('income',0):,.0f} руб., расходы {prev_stats.get('expenses',0):,.0f} руб.\n\n"
        "3 предложения: что изменилось критично и один совет. Без эмодзи."
    )

    try:
        return await _generate(prompt, system="Финансовый аналитик. Коротко. Без эмодзи.")
    except Exception as e:
        logging.error(f"get_trends_analysis error: {e}")
        return "Не удалось загрузить тренды."
