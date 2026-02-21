import os
import json
import logging
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


async def _generate(prompt: str) -> str:
    """Async вызов Groq (openai/gpt-oss-20b)."""
    client = _get_client()
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
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


async def parse_transaction(text: str) -> dict | None:
    prompt = f"""Ты помощник для финансового трекера. Пользователь написал: "{text}"

Твоя задача: определить, СООБЩАЕТ ли пользователь о УЖЕ СВЕРШИВШЕЙСЯ трате или доходе (факт), а не спрашивает и не рассуждает.

КРИТИЧНО — это НЕ транзакция (возвращай is_transaction: false):
- Любой вопрос: "стоит ли купить", "посоветуй", "можно ли", "как", "сколько стоит", "хватит ли", "выгодно ли", "имеет смысл"
- Просьба совета: "стоит ли мне купить брюки за 50 тысяч" — это вопрос, НЕ запись расхода
- Планы и рассуждения: "хочу купить", "думаю купить", "планирую", "собираюсь взять"
- Если есть "?" в конце или по смыслу это вопрос — НЕ транзакция

Транзакция — только когда пользователь констатирует факт: уже потратил, уже получил.
Примеры транзакций: "потратил 500", "купил кофе 180", "заплатил за такси", "получил зарплату 50000"
Примеры НЕ транзакций: "стоит ли купить брюки за 50 тысяч", "хочу купить за 5000", "посоветуй, брать ли"

Если это транзакция — верни JSON:
{{"is_transaction": true, "type": "expense" или "income", "amount": число, "category": одна из списка, "description": краткое описание}}

Категории расходов: {", ".join(EXPENSE_CATEGORIES)}
Категории доходов: {", ".join(INCOME_CATEGORIES)}

Если это НЕ транзакция — верни:
{{"is_transaction": false}}

Отвечай ТОЛЬКО валидным JSON без пояснений и без markdown-блоков."""

    try:
        raw = await _generate(prompt)
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return result if result.get("is_transaction") else None
    except Exception as e:
        logging.error(f"parse_transaction error: {e}")
        return None


async def get_ai_advice(stats: dict, user_name: str = "друг") -> str:
    by_category = stats.get("by_category", {})
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)

    if not by_category and income == 0:
        return "Цифр пока кот наплакал. Погоняй расходы пару дней — тогда разберём по косточкам."

    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    prompt = f"""Ты дружелюбный личный финансовый советник. Обращайся к пользователю на "ты".

Данные за последний месяц:
- Доходы: {income:,.0f} ₽
- Расходы: {expenses:,.0f} ₽
- Баланс: {balance:,.0f} ₽
- Расходы по категориям:
{cat_list if cat_list else "Данных нет"}

Напиши короткий (5-7 предложений) персональный анализ:
1. Оцени общую картину (позитивно, даже если есть проблемы)
2. Выдели 1-2 категории где можно сэкономить (с конкретными цифрами)
3. Дай 1 практический совет на следующий месяц
4. Заверши мотивирующей фразой

Пиши живо и по-человечески, с лёгкой иронией. Без эмодзи."""

    try:
        return await _generate(prompt)
    except Exception as e:
        logging.error(f"get_ai_advice error: {e}")
        return f"Совет не выдали — что-то пошло не так: {str(e)}"


def build_datetime_context(now_dt=None):
    """Строка с текущей датой и временем для контекста ИИ."""
    from datetime import datetime, timezone
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    # Для читаемости: локальное время (МСК +3)
    try:
        import pytz
        tz = pytz.timezone("Europe/Moscow")
        local = now_dt.astimezone(tz)
    except Exception:
        local = now_dt
    weekdays_ru = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    wd = weekdays_ru[local.weekday()]
    return f"Сейчас: {local.strftime('%d.%m.%Y')}, {wd}, {local.strftime('%H:%M')} МСК."


async def chat_with_ai(
    user_message: str,
    stats: dict,
    payments: list,
    context_extra: str = "",
    budgets: list = None,
) -> str:
    by_category = stats.get("by_category", {})
    by_income_category = stats.get("by_income_category", {})
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)

    date_time_block = build_datetime_context()
    if context_extra:
        date_time_block = date_time_block + "\n" + context_extra

    payments_text = ""
    if payments:
        payments_text = "\nОбязательные платежи (напоминания):\n" + "\n".join(
            [f"- {p['name']}: {p['amount']:,.0f} ₽ ({p['day_of_month']}-е число)" for p in payments]
        )

    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])
    inc_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_income_category.items()]) if by_income_category else "Нет данных"

    budgets_text = ""
    if budgets:
        budgets_text = "\nЛимиты по категориям:\n" + "\n".join(
            [f"- {b['category']}: лимит {b['limit_amount']:,.0f} ₽/мес" for b in budgets]
        )

    prompt = f"""Ты персональный финансовый ассистент. У тебя есть дата/время и напоминания — используй их в ответах.

{date_time_block}
{payments_text}
{budgets_text}

Финансовые данные за последние 30 дней:
- Доходы: {income:,.0f} ₽
- Расходы: {expenses:,.0f} ₽
- Баланс: {balance:,.0f} ₽
- Расходы по категориям:
{cat_list if cat_list else "Нет данных"}
- Доходы по категориям:
{inc_list}

Вопрос пользователя: {user_message}

Ты умеешь: отвечать на вопросы о тратах и доходах, подсказывать «сколько потратил на X», сравнивать категории, напоминать о ближайших платежах и дне зарплаты, оценивать укладывание в лимиты, давать короткие советы. Отвечай на основе этих данных, с учётом текущей даты. Кратко и по делу, с лёгкой иронией. Без эмодзи."""

    try:
        return await _generate(prompt)
    except Exception as e:
        logging.error(f"chat_with_ai error: {e}")
        return f"Не срослось: {str(e)}"


async def generate_weekly_ai_report(stats: dict) -> str | None:
    if stats.get("transactions_count", 0) == 0:
        return None

    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)
    by_category = stats.get("by_category", {})
    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    prompt = f"""Финансовые данные за неделю:
Доходы: {income:,.0f} ₽, Расходы: {expenses:,.0f} ₽, Баланс: {balance:,.0f} ₽
Категории: {cat_list if cat_list else "нет данных"}

Напиши краткий (3-4 предложения) еженедельный итог с одним конкретным советом на следующую неделю.
Стиль: дружеский, с лёгкой иронией. Без эмодзи."""

    try:
        return await _generate(prompt)
    except Exception as e:
        logging.error(f"weekly report error: {e}")
        return None


async def get_smart_budget_advice(stats: dict, days_until_salary: int, mandatory_expenses: float, salary_day: int) -> str:
    """Умный план бюджета до зарплаты."""
    balance = stats.get("balance", 0)
    expenses = stats.get("expenses", 0)
    by_category = stats.get("by_category", {})
    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    free_money = balance - mandatory_expenses
    daily_budget = free_money / days_until_salary if days_until_salary > 0 else 0

    prompt = f"""Ты финансовый советник. Составь короткий практичный план бюджета.

Данные:
- Текущий баланс (доходы минус расходы за месяц): {balance:,.0f} ₽
- Дней до следующей зарплаты ({salary_day}-е число): {days_until_salary}
- Обязательные платежи до зарплаты: {mandatory_expenses:,.0f} ₽
- Свободные деньги: {free_money:,.0f} ₽
- Дневной бюджет: {daily_budget:,.0f} ₽/день
- Расходы по категориям за месяц:
{cat_list if cat_list else "нет данных"}

Напиши совет (4-5 предложений):
1. Сколько можно тратить в день
2. На какой категории стоит сэкономить (на основе данных)
3. Сколько отложить если есть возможность
Стиль: дружеский, конкретный, без занудства. Используй цифры."""

    try:
        return await _generate(prompt)
    except Exception as e:
        logging.error(f"smart budget advice error: {e}")
        return None
