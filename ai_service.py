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
