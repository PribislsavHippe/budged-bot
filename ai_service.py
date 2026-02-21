import os
import json
import logging
from datetime import datetime, timezone, date
from groq import AsyncGroq

_client: AsyncGroq | None = None
GROQ_MODEL = "openai/gpt-oss-20b"


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY не задан в окружении")
        _client = AsyncGroq(api_key=api_key)
    return _client


async def _generate(prompt: str, system: str = None) -> str:
    client = _get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
    )
    return (response.choices[0].message.content or "").strip()


EXPENSE_CATEGORIES = [
    "Еда", "Транспорт", "Жильё", "Развлечения",
    "Здоровье", "Одежда", "Связь", "Образование",
    "Обязательные", "Прочее"
]

INCOME_CATEGORIES = [
    "Зарплата", "Оплата за неделю", "Аванс", "Частичная оплата",
    "Фриланс", "Подработка", "Подарок", "Инвестиции", "Прочее"
]


def build_datetime_context(now_dt=None) -> str:
    """Строка с текущей датой для контекста ИИ."""
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


async def parse_transaction(text: str) -> dict | None:
    """Распознаём транзакцию через ИИ. Только свершившиеся факты."""
    now_ctx = build_datetime_context()
    prompt = f"""Пользователь написал: "{text}"

{now_ctx}

Это ФАКТ уже совершённой траты или полученного дохода? Или это что-то другое?

НЕ транзакция — верни is_transaction: false:
- Вопросы и рассуждения (есть "?", "стоит ли", "хватит ли", "посоветуй", "как")
- Упоминание будущих событий: "мне предстоят платежи", "планируется", "9 числа будет"
- Числа, которые являются датами, а не суммами ("9 и 11 числа" — это даты!)
- Сообщения о контексте без факта траты
- Планы: "хочу купить", "думаю взять"

Транзакция — только свершившийся факт: "потратил 500", "купил кофе 180", "получил зарплату 50000"

Если транзакция: {{"is_transaction": true, "type": "expense" или "income", "amount": число, "category": из списка, "description": краткое описание}}
Если нет: {{"is_transaction": false}}

Категории расходов: {", ".join(EXPENSE_CATEGORIES)}
Категории доходов: {", ".join(INCOME_CATEGORIES)}

Только JSON, без пояснений."""

    try:
        raw = await _generate(prompt)
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return result if result.get("is_transaction") else None
    except Exception as e:
        logging.error(f"parse_transaction error: {e}")
        return None


async def chat_with_ai(
    user_message: str,
    stats: dict,
    payments: list,
    context_extra: str = "",
    budgets: list = None,
    planned_income: list = None,
) -> str:
    by_category = stats.get("by_category", {})
    by_income_category = stats.get("by_income_category", {})
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)

    date_ctx = build_datetime_context()
    today = date.today()

    # Платежи с правильным "прошёл/впереди" относительно сегодня
    payments_text = ""
    if payments:
        future = sorted([p for p in payments if p["day_of_month"] >= today.day], key=lambda x: x["day_of_month"])
        past = sorted([p for p in payments if p["day_of_month"] < today.day], key=lambda x: x["day_of_month"])
        if future:
            lines = [f"  {p['day_of_month']}-е: {p['name']} {p['amount']:,.0f} ₽" for p in future]
            payments_text += "\nПлатежи впереди в этом месяце:\n" + "\n".join(lines)
        if past:
            lines = [f"  {p['day_of_month']}-е: {p['name']} {p['amount']:,.0f} ₽" for p in past]
            payments_text += "\nПлатежи этого месяца уже прошли:\n" + "\n".join(lines)

    # Планируемые доходы/расходы с пометками
    planned_text = ""
    if planned_income:
        lines = []
        for p in planned_income:
            d_str = p.get("expected_date", "")[:10]
            try:
                d = date.fromisoformat(d_str)
                rel = "впереди" if d >= today else "уже прошло"
            except Exception:
                rel = ""
            desc = f" ({p.get('description', '')})" if p.get("description") else ""
            lines.append(f"  {d_str}: {p.get('amount', 0):,.0f} ₽{desc} [{rel}]")
        planned_text = "\nПланируемые записи:\n" + "\n".join(lines)

    cat_list = "\n".join([f"  {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])
    inc_list = "\n".join([f"  {cat}: {amt:,.0f} ₽" for cat, amt in by_income_category.items()]) if by_income_category else "  Нет данных"

    budgets_text = ""
    if budgets:
        budgets_text = "\nЛимиты по категориям:\n" + "\n".join(
            [f"  {b['category']}: {b['limit_amount']:,.0f} ₽/мес" for b in budgets]
        )

    system = """Ты персональный финансовый ассистент — умный, немного нахальный, но реально полезный.
Обращайся на «ты». Без эмодзи. Кратко и конкретно.
ВАЖНО: используй текущую дату (она указана) для всех расчётов. 
Платёж "впереди" = его число >= сегодняшнему числу месяца.
Платёж "прошёл" = его число < сегодняшнего числа месяца."""

    user_prompt = f"""{date_ctx}
{context_extra}
{payments_text}
{planned_text}
{budgets_text}

Финансы за последние 30 дней:
- Доходы: {income:,.0f} ₽
- Расходы: {expenses:,.0f} ₽
- Баланс (доходы − расходы): {balance:,.0f} ₽
- Расходы по категориям:
{cat_list if cat_list else "  Нет данных"}
- Доходы по категориям:
{inc_list}

Вопрос пользователя: {user_message}"""

    try:
        return await _generate(user_prompt, system=system)
    except Exception as e:
        logging.error(f"chat_with_ai error: {e}")
        return f"Что-то сломалось на моей стороне: {str(e)}"


async def get_ai_advice(stats: dict, user_name: str = "друг") -> str:
    by_category = stats.get("by_category", {})
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)

    if not by_category and income == 0:
        return "Цифр пока кот наплакал. Погоняй расходы пару дней — тогда разберём по косточкам."

    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    prompt = f"""Данные за месяц:
Доходы: {income:,.0f} ₽, Расходы: {expenses:,.0f} ₽, Баланс: {balance:,.0f} ₽
По категориям:\n{cat_list if cat_list else "Нет данных"}

5-7 предложений: оцени картину, выдели 1-2 категории где сэкономить с цифрами, дай 1 практический совет, заверши мотивирующей фразой."""

    try:
        return await _generate(prompt, system="Финансовый советник. На «ты», живо, с иронией. Без эмодзи.")
    except Exception as e:
        logging.error(f"get_ai_advice error: {e}")
        return f"Совет не выдали: {str(e)}"


async def generate_weekly_ai_report(stats: dict) -> str | None:
    if stats.get("transactions_count", 0) == 0:
        return None

    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)
    by_category = stats.get("by_category", {})
    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    prompt = f"""Финансы за неделю:
Доходы: {income:,.0f} ₽, Расходы: {expenses:,.0f} ₽, Баланс: {balance:,.0f} ₽
Категории: {cat_list if cat_list else "нет данных"}

3-4 предложения: итог недели + один конкретный совет на следующую."""

    try:
        return await _generate(prompt, system="Финансовый советник. На «ты», с иронией. Без эмодзи.")
    except Exception as e:
        logging.error(f"weekly report error: {e}")
        return None


async def get_smart_budget_advice(stats: dict, days_until_salary: int, mandatory_expenses: float, salary_day: int) -> str:
    balance = stats.get("balance", 0)
    by_category = stats.get("by_category", {})
    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])
    free_money = balance - mandatory_expenses
    daily_budget = free_money / days_until_salary if days_until_salary > 0 else 0

    prompt = f"""Баланс: {balance:,.0f} ₽. До зарплаты ({salary_day}-е): {days_until_salary} дней.
Обязательные платежи до зарплаты: {mandatory_expenses:,.0f} ₽. Свободно: {free_money:,.0f} ₽. Дневной бюджет: {daily_budget:,.0f} ₽/день.
Расходы по категориям:\n{cat_list if cat_list else "нет данных"}

4-5 предложений: сколько тратить в день, на чём сэкономить, сколько отложить."""

    try:
        return await _generate(prompt, system="Финансовый советник. Конкретно, с цифрами. Без эмодзи.")
    except Exception as e:
        logging.error(f"smart budget advice error: {e}")
        return None


async def evaluate_goal(
    stats: dict, payments: list, planned_income: list,
    target_amount: float, target_months: int, monthly_amount: float, salary_days: list,
) -> str:
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)
    payments_sum = sum(p.get("amount", 0) for p in payments) if payments else 0
    planned_sum = sum(p.get("amount", 0) for p in planned_income) if planned_income else 0

    prompt = f"""Хочет накопить {target_amount:,.0f} ₽ за {target_months} мес. (откладывать {monthly_amount:,.0f} ₽/мес).
Доходы: {income:,.0f} ₽, Расходы: {expenses:,.0f} ₽, Баланс: {balance:,.0f} ₽
Обязательные платежи: ~{payments_sum:,.0f} ₽/мес. Дни зарплаты: {salary_days}.

3-5 предложений: реалистична ли цель, что может помешать, один практический совет."""

    try:
        return await _generate(prompt, system="Финансовый советник. С лёгкой иронией. Без эмодзи.")
    except Exception as e:
        logging.error(f"evaluate_goal error: {e}")
        return "Цель сохранена. Откладывай сразу в день зарплаты — пока деньги ещё не успели найти себе занятие."


async def parse_onboarding_payments(text: str) -> list[dict]:
    """Парсим список регулярных платежей из свободного текста для онбординга."""
    prompt = f"""Пользователь описывает свои регулярные обязательные платежи: "{text}"

Извлеки все платежи. Для каждого определи:
- name: название (аренда, Netflix, ипотека и т.д.)
- amount: сумма числом
- day: день месяца (если указан, иначе 1)

Верни JSON массив: [{{"name": "...", "amount": число, "day": число}}, ...]
Если ничего не распознал — верни: []
Только JSON, без пояснений."""

    try:
        raw = await _generate(prompt)
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception as e:
        logging.error(f"parse_onboarding_payments error: {e}")
        return []


# ─── МАССОВЫЙ ВВОД ───────────────────────────────────────────────────────────

async def parse_bulk_transactions(text: str) -> list[dict]:
    """Парсит несколько транзакций из одного сообщения."""
    now_ctx = build_datetime_context()
    prompt = (
        f'Пользователь написал: "{text}"\n\n'
        f"{now_ctx}\n\n"
        "Это список трат или доходов. Разбей на отдельные транзакции.\n"
        "Для каждой: {\"type\": \"expense\" или \"income\", \"amount\": число, \"category\": из списка, \"description\": краткое}\n\n"
        f"Категории расходов: {', '.join(EXPENSE_CATEGORIES)}\n"
        f"Категории доходов: {', '.join(INCOME_CATEGORIES)}\n\n"
        "Верни JSON массив. Если одна транзакция — массив из одного элемента.\n"
        "Если это НЕ список трат (вопрос, рассуждение) — верни: []\n"
        "Только JSON, без пояснений."
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


# ─── ТРАНСКРИПЦИЯ ГОЛОСА ──────────────────────────────────────────────────────

async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Транскрибирует голосовое сообщение через Groq Whisper API."""
    import io
    client = _get_client()
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        transcription = await client.audio.transcriptions.create(
            file=(filename, audio_file),
            model="whisper-large-v3",
            language="ru",
            response_format="text",
        )
        return str(transcription).strip() if transcription else None
    except Exception as e:
        logging.error(f"transcribe_voice error: {e}")
        return None


# ─── РАСПОЗНАВАНИЕ ФОТО ЧЕКА ──────────────────────────────────────────────────

async def parse_receipt_photo(image_base64: str) -> list[dict]:
    """Парсит чек из фото через vision-модель."""
    client = _get_client()
    try:
        cats = ", ".join(EXPENSE_CATEGORIES)
        response = await client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                        },
                        {
                            "type": "text",
                            "text": (
                                "Это фото чека. Определи итоговую сумму, магазин и тип покупки.\n"
                                f"Верни JSON: [{{\"type\": \"expense\", \"amount\": число, \"category\": из списка, \"description\": \"магазин\"}}]\n"
                                f"Категории: {cats}\n"
                                "Если не разобрал — верни: []\nТолько JSON."
                            )
                        }
                    ]
                }
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return [r for r in result if r.get("amount")] if isinstance(result, list) else []
    except Exception as e:
        logging.error(f"parse_receipt_photo error: {e}")
        return []


# ─── УМНЫЙ ДАШБОРД ───────────────────────────────────────────────────────────

async def get_smart_dashboard(stats: dict, payments: list, salary_days: list, planned: list) -> str | None:
    """Генерирует умный дашборд — выводы, а не цифры."""
    from datetime import date
    from calendar import monthrange
    today = date.today()
    days_in_month = monthrange(today.year, today.month)[1]
    month_progress = today.day / days_in_month

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
    projected = expenses / month_progress if month_progress > 0 else expenses

    cat_lines = "\n".join([f"  {cat}: {amt:,.0f} руб." for cat, amt in by_category.items()])
    date_ctx = build_datetime_context()

    prompt = (
        f"{date_ctx}\n"
        f"День {today.day} из {days_in_month} ({month_progress*100:.0f}% месяца).\n\n"
        f"Доходы: {income:,.0f} руб.\n"
        f"Расходы: {expenses:,.0f} руб.\n"
        f"Баланс: {balance:,.0f} руб.\n"
        f"Свободных до зарплаты: {free:,.0f} руб.\n"
        f"До зарплаты ({next_salary_day}-го): {days_to_salary} дней.\n"
        f"Дневной бюджет: {daily:,.0f} руб./день.\n"
        f"Платежи впереди: {future_pmts_sum:,.0f} руб.\n"
        f"Прогноз расходов на месяц: {projected:,.0f} руб.\n"
        f"Расходы по категориям:\n{cat_lines if cat_lines else '  нет данных'}\n\n"
        "Напиши дашборд 6-8 строк: главный вывод (хватит/не хватит, дневной бюджет), "
        "тревожные категории если есть, прогноз к зарплате, один совет. Конкретные цифры. Без эмодзи."
    )

    try:
        return await _generate(prompt, system="Финансовый советник. Коротко и по делу. Без эмодзи.")
    except Exception as e:
        logging.error(f"get_smart_dashboard error: {e}")
        return None


# ─── ТРЕНДЫ ──────────────────────────────────────────────────────────────────

async def get_trends_analysis(current_stats: dict, prev_stats: dict) -> str:
    """Сравнивает текущий период с предыдущим и даёт вывод."""
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
            lines.append(f"{cat}: {curr:,.0f} руб. (новая категория)")

    cats_text = "\n".join(lines) if lines else "Нет данных для сравнения"
    prompt = (
        f"Сравнение расходов месяц к месяцу:\n{cats_text}\n\n"
        f"Текущий: доходы {current_stats.get('income',0):,.0f} руб., расходы {current_stats.get('expenses',0):,.0f} руб.\n"
        f"Прошлый: доходы {prev_stats.get('income',0):,.0f} руб., расходы {prev_stats.get('expenses',0):,.0f} руб.\n\n"
        "3-4 предложения: что изменилось критично, общий вывод. Без эмодзи."
    )

    try:
        return await _generate(prompt, system="Финансовый аналитик. Конкретно. Без эмодзи.")
    except Exception as e:
        logging.error(f"get_trends_analysis error: {e}")
        return "Не удалось загрузить анализ трендов."
