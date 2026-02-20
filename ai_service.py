import os
import json
import logging
import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-flash-latest")

EXPENSE_CATEGORIES = [
    "🍕 Еда", "🚗 Транспорт", "🏠 Жильё", "🎮 Развлечения",
    "💊 Здоровье", "👕 Одежда", "📱 Связь", "📚 Образование",
    "💳 Обязательные", "🛒 Прочее"
]

INCOME_CATEGORIES = [
    "💼 Зарплата", "💰 Аванс", "💻 Фриланс", "🎁 Подарок", "📈 Прочее"
]


async def _generate(prompt: str) -> str:
    """Нативный async вызов Gemini."""
    response = await model.generate_content_async(prompt)
    return response.text.strip()


async def parse_transaction(text: str) -> dict | None:
    prompt = f"""Ты помощник для финансового трекера. Пользователь написал: "{text}"

Определи, описывает ли это финансовую транзакцию (трату или доход).

Если ДА — верни JSON:
{{"is_transaction": true, "type": "expense" или "income", "amount": число, "category": одна из списка, "description": краткое описание}}

Категории расходов: {", ".join(EXPENSE_CATEGORIES)}
Категории доходов: {", ".join(INCOME_CATEGORIES)}

Если НЕТ — верни:
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
        return "📊 Пока данных маловато для анализа. Вноси расходы каждый день — и через неделю дам подробный разбор!"

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

Пиши живо и по-человечески, без занудства. Используй эмодзи умеренно."""

    try:
        return await _generate(prompt)
    except Exception as e:
        logging.error(f"get_ai_advice error: {e}")
        return f"😔 Ошибка при получении совета: {str(e)}"


async def chat_with_ai(user_message: str, stats: dict, payments: list) -> str:
    by_category = stats.get("by_category", {})
    income = stats.get("income", 0)
    expenses = stats.get("expenses", 0)
    balance = stats.get("balance", 0)

    payments_text = ""
    if payments:
        payments_text = "\nОбязательные платежи:\n" + "\n".join(
            [f"- {p['name']}: {p['amount']:,.0f} ₽ ({p['day_of_month']}-е число)" for p in payments]
        )

    cat_list = "\n".join([f"- {cat}: {amt:,.0f} ₽" for cat, amt in by_category.items()])

    prompt = f"""Ты персональный финансовый ассистент. Отвечай на "ты", кратко и по делу.

Финансовые данные пользователя за последние 30 дней:
- Доходы: {income:,.0f} ₽
- Расходы: {expenses:,.0f} ₽
- Баланс: {balance:,.0f} ₽
- Расходы по категориям:
{cat_list if cat_list else "Нет данных"}
{payments_text}

Вопрос пользователя: {user_message}

Отвечай только на основе этих данных. Если данных нет — скажи что нужно больше записей.
Будь дружелюбным и конкретным, используй цифры из данных пользователя."""

    try:
        return await _generate(prompt)
    except Exception as e:
        logging.error(f"chat_with_ai error: {e}")
        return f"😔 Ошибка: {str(e)}"


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
Стиль: дружеский, с эмодзи, без занудства."""

    try:
        return await _generate(prompt)
    except Exception as e:
        logging.error(f"weekly report error: {e}")
        return None
