"""
budget_engine.py — Умный движок формирования бюджетов.

Принципы:
1. Конкретные цифры от пользователя неприкосновенны (STRICT)
2. Некоторые категории нельзя срезать (FIXED): Еда, Транспорт*, Связь*, Хобби*
3. Образование — только если упомянуто пользователем
4. Одежда — только если есть профицит
5. Здоровье — низкий приоритет при дефиците (не медицина, а общий бюджет)
6. Кредиты/Обязательные — вычитаются первыми как scheduled_payments

* = FIXED только если пользователь называл конкретную цифру
"""

from __future__ import annotations
import re
import logging
from dataclasses import dataclass, field

# ─── ИЕРАРХИЯ ПРИОРИТЕТОВ ────────────────────────────────────────────────────
#  0 = самый высокий приоритет (режем последним)
#  9 = режем первым

CATEGORY_PRIORITY = {
    "Еда":              0,
    "Транспорт":        1,
    "Подписки":            2,
    "Хобби":            3,   # неприкосновенно если суммы указаны явно
    "Кафе и рестораны": 4,
    "Здоровье":         5,   # низкий при дефиците (понижен по просьбе)
    "Образование":      6,   # только если упомянуто
    "Прочее":           7,
    "Одежда":           8,   # только при профиците
}

# Минимально необходимые суммы (нижний предел, нельзя урезать ниже)
ABSOLUTE_MINIMUMS: dict[str, float] = {
    "Еда":              10_000,
    "Транспорт":         2_000,
    "Подписки":               400,
    "Хобби":               500,
    "Кафе и рестораны":  1_000,
    "Здоровье":          1_000,
    "Образование":           0,
    "Прочее":              500,
    "Одежда":                0,
}

# Доли от свободных денег (если нет конкретных данных пользователя)
DEFAULT_SHARES: dict[str, float] = {
    "Еда":              0.28,
    "Транспорт":        0.10,
    "Подписки":            0.02,
    "Хобби":            0.08,
    "Кафе и рестораны": 0.10,
    "Здоровье":         0.04,   # понижено (было 0.07)
    "Образование":      0.00,   # по умолчанию не включаем
    "Прочее":           0.06,
    "Одежда":           0.00,   # по умолчанию не включаем
}


@dataclass
class ParsedExpense:
    """Распознанная трата из описания пользователя."""
    category: str
    amount: float
    description: str
    is_strict: bool = True  # True = пользователь назвал конкретную цифру


@dataclass
class BudgetResult:
    budgets: dict[str, float]
    is_deficit: bool
    free_money: float
    scheduled_sum: float
    monthly_income: float
    cut_categories: list[str] = field(default_factory=list)
    skipped_categories: list[str] = field(default_factory=list)
    strict_categories: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ─── ПАРСИНГ ТРАТ ПОЛЬЗОВАТЕЛЯ (без AI, по ключевым словам) ──────────────────

_HOBBY_KEYWORDS = [
    "танц", "танго", "балет", "хореограф", "вокал", "пение",
    "гитар", "фортепиан", "пианино", "скрипк", "рисован",
    "живопись", "художник", "акварел", "вышивание", "вязание",
    "шитьё", "шитье", "рукоделие", "фотограф",
    "скалолазание", "альпинизм", "карате", "дзюдо", "бокс",
    "единоборств", "велосипед", "верховая", "дайвинг", "серфинг",
    "горные лыжи", "сноуборд", "секция", "кружок", "студия",
    "школа танцев", "абонемент", "занятие",
    "спортзал", "фитнес", "тренажёр", "тренажер", "йога", "бассейн",
    "тренировка", "персональный тренер", "кроссфит", "пилатес",
    "растяжка", "теннис", "футбол", "хобби",
]

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Еда": [
        "еда", "продукт", "супермаркет", "магазин еды", "магнит",
        "пятёрочка", "перекрёсток", "вкусвилл", "ашан", "лента",
        "молоко", "мясо", "хлеб", "продуктовый",
    ],
    "Транспорт": [
        "такси", "метро", "автобус", "бензин", "заправка", "транспорт",
        "проезд", "маршрутка", "электричка", "убер", "каршеринг",
        "мойка", "шиномонтаж",
    ],
    "Подписки": [
        "мтс", "билайн", "мегафон", "теле2", "йота", "тинькофф мобайл",
        "интернет", "подписк", "мобильн", "тариф", "сим", "связь",
        "яндекс плюс", "яндекс+", "spotify", "netflix", "нетфликс",
        "apple music", "youtube premium", "ютуб премиум", "vk music",
        "дeezer", "okko", "иви", "кинопоиск", "more.tv",
    ],
    "Хобби": _HOBBY_KEYWORDS,
    "Кафе и рестораны": [
        "кафе", "ресторан", "кофейня", "обед в кафе", "доставка еды",
        "яндекс еда", "самокат", "пицца", "суши", "бургер",
        "фастфуд", "макдоналдс", "kfc",
    ],
    "Здоровье": [
        "аптека", "аптек", "врач", "клиника", "больниц", "лекарств",
        "медицин", "витамин", "таблетк", "стоматолог", "массаж",
        "мрт", "узи", "анализ",
    ],
    "Образование": [
        "курс", "обучение", "учёба", "учеба", "репетитор", "книг",
        "вебинар", "skillbox", "нетология", "coursera", "udemy",
        "языковые курсы", "образование",
    ],
    "Одежда": [
        "одежд", "обувь", "wildberries", "wb", "lamoda", "ozon одежда",
        "шопинг", "куртк", "джинсы", "платье",
    ],
    "Кредиты": [
        "кредит", "ипотека", "займ", "рассрочка", "погашение кредита",
        "платёж по кредиту",
    ],
}


def _detect_category(text: str) -> str:
    lower = text.lower()
    best, best_score = "Прочее", 0
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > best_score:
            best_score = score
            best = cat
    return best


def _extract_amount_from_fragment(text: str) -> float | None:
    """Нормализует и извлекает сумму из фрагмента текста."""
    t = text.lower().replace(",", ".").replace("\xa0", " ")
    t = re.sub(r'(\d+(?:\.\d+)?)\s*(?:тысяч(?:и|а)?|тыс\.?|к)\b',
               lambda m: str(int(float(m.group(1)) * 1000)), t)
    t = re.sub(r'(\d+(?:\.\d+)?)\s*(?:млн|миллион)\b',
               lambda m: str(int(float(m.group(1)) * 1_000_000)), t)
    nums = re.findall(r'\b(\d[\d\s]{0,6}(?:\.\d{1,2})?)\b', t)
    for n in nums:
        try:
            val = float(n.replace(" ", ""))
            if 100 <= val <= 5_000_000:
                return val
        except Exception:
            pass
    return None


def parse_user_spending(text: str) -> list[ParsedExpense]:
    """
    Разбирает свободное описание трат пользователя.
    Возвращает список ParsedExpense с категориями и суммами.

    Примеры:
      "еда 20000, связь 800, танцы 6900"
      "Трачу много на продукты — примерно 15-18 тысяч, такси 5000 в месяц"
    """
    results: list[ParsedExpense] = []
    seen_categories: set[str] = set()

    # Разбиваем на фрагменты по запятой, точке с запятой, переводу строки
    fragments = re.split(r'[,;\n]+', text)

    for frag in fragments:
        frag = frag.strip()
        if len(frag) < 3:
            continue

        amount = _extract_amount_from_fragment(frag)
        if amount is None:
            # Фраза без суммы — запоминаем категорию для включения в бюджет
            cat = _detect_category(frag)
            if cat != "Прочее" and cat not in seen_categories:
                # Добавим с нулём — сигнал "упомянута, но без суммы"
                results.append(ParsedExpense(
                    category=cat, amount=0.0,
                    description=frag[:60], is_strict=False
                ))
                seen_categories.add(cat)
            continue

        cat = _detect_category(frag)
        if cat in seen_categories:
            # Обновляем если новое значение больше (берём максимум)
            for r in results:
                if r.category == cat:
                    if amount > r.amount:
                        r.amount = amount
                        r.is_strict = True
            continue

        results.append(ParsedExpense(
            category=cat, amount=amount,
            description=frag[:60], is_strict=True
        ))
        seen_categories.add(cat)

    return results


# ─── ЯДРО ФОРМИРОВАНИЯ БЮДЖЕТОВ ──────────────────────────────────────────────

def build_budgets(
    monthly_income: float,
    scheduled_payments: list[dict],
    user_expenses: list[ParsedExpense] | None = None,
    current_balance: float = 0.0,
) -> BudgetResult:
    """
    Главная функция: формирует бюджеты по приоритетам.

    Алгоритм:
    1. Вычитаем обязательные платежи
    2. Накладываем STRICT значения (конкретные суммы пользователя)
    3. Распределяем остаток по DEFAULT_SHARES, учитывая приоритеты
    4. При дефиците срезаем низкоприоритетные категории
    """
    scheduled_sum = sum(float(p.get("amount", 0)) for p in (scheduled_payments or []))
    free = monthly_income - scheduled_sum

    result = BudgetResult(
        budgets={},
        is_deficit=(free < 0),
        free_money=max(free, 0),
        scheduled_sum=scheduled_sum,
        monthly_income=monthly_income,
    )

    if free <= 0:
        result.notes.append(
            f"Обязательные платежи ({scheduled_sum:,.0f} ₽) превышают доход. "
            "Бюджет не сформирован — нужно оптимизировать платежи."
        )
        return result

    expenses_by_cat: dict[str, ParsedExpense] = {}
    if user_expenses:
        for exp in user_expenses:
            if exp.category in expenses_by_cat:
                if exp.amount > expenses_by_cat[exp.category].amount:
                    expenses_by_cat[exp.category] = exp
            else:
                expenses_by_cat[exp.category] = exp

    # ── Шаг 1: STRICT — фиксируем конкретные суммы пользователя ──────────────
    remaining = free
    budgets: dict[str, float] = {}

    for cat, exp in expenses_by_cat.items():
        if exp.is_strict and exp.amount > 0:
            # Уважаем конкретную цифру пользователя — не срезаем
            if cat in ("Кредиты", "Обязательные"):
                # Кредиты уже в scheduled_payments, не дублируем
                continue
            budgets[cat] = exp.amount
            remaining -= exp.amount
            result.strict_categories.append(cat)

    if remaining < 0:
        # Даже STRICT категории превышают бюджет — предупреждаем, не срезаем
        result.is_deficit = True
        result.notes.append(
            "Сумма указанных трат превышает свободные деньги после платежей. "
            "Советую пересмотреть расходы."
        )
        result.budgets = budgets
        return result

    # ── Шаг 2: DEFAULT — распределяем остаток по приоритетам ─────────────────
    # Сортируем категории по приоритету
    cats_to_fill = [
        cat for cat in sorted(DEFAULT_SHARES.keys(), key=lambda c: CATEGORY_PRIORITY.get(c, 9))
        if cat not in budgets  # уже установленные STRICT не трогаем
    ]

    for cat in cats_to_fill:
        default_share = DEFAULT_SHARES.get(cat, 0)
        if default_share == 0:
            # Образование и Одежда не включаем без явного упоминания или профицита
            if cat == "Образование":
                if cat in expenses_by_cat:
                    # Упомянуто пользователем (без суммы) — даём минимум
                    budgets[cat] = ABSOLUTE_MINIMUMS.get(cat, 0)
                    result.notes.append(f"Образование добавлено т.к. упомянуто.")
                else:
                    result.skipped_categories.append(cat)
                continue
            if cat == "Одежда":
                # Добавим только при профиците (remaining > 20% от free)
                if remaining > free * 0.20:
                    target = round(free * 0.08)
                    budgets[cat] = min(target, remaining)
                    remaining -= budgets[cat]
                else:
                    result.skipped_categories.append(cat)
                continue

        target = round(free * default_share)
        minimum = ABSOLUTE_MINIMUMS.get(cat, 0)

        # Если пользователь упоминал категорию без суммы — даём стандартную долю
        if cat in expenses_by_cat and not expenses_by_cat[cat].is_strict:
            target = max(target, minimum)

        if remaining <= 0:
            result.skipped_categories.append(cat)
            continue

        if remaining < minimum and minimum > 0:
            budgets[cat] = round(remaining)
            remaining = 0
            result.notes.append(f"{cat}: даётся меньше минимума — денег не хватает.")
        else:
            amount = min(target, remaining)
            if amount < minimum and minimum > 0:
                amount = minimum
            budgets[cat] = round(amount)
            remaining -= amount

    # ── Шаг 3: Проверка на дефицит — срезаем низкоприоритетные ──────────────
    if remaining < 0:
        result.is_deficit = True
        # Срезаем в обратном порядке приоритета
        for cat in reversed(cats_to_fill):
            if remaining >= 0:
                break
            if cat in result.strict_categories:
                continue  # STRICT не трогаем
            if cat not in budgets:
                continue
            minimum = ABSOLUTE_MINIMUMS.get(cat, 0)
            current = budgets[cat]
            can_cut = max(0, current - minimum)
            cut = min(can_cut, abs(remaining))
            if cut > 0:
                budgets[cat] -= cut
                remaining += cut
                if budgets[cat] <= 0:
                    del budgets[cat]
                    result.cut_categories.append(cat)
                else:
                    result.cut_categories.append(cat)

    result.budgets = {k: v for k, v in budgets.items() if v > 0}
    result.is_deficit = remaining < -500  # допускаем небольшую погрешность
    return result


def format_budget_result(r: BudgetResult) -> str:
    """Форматирует результат для показа пользователю."""
    lines = []
    total = sum(r.budgets.values())

    if r.is_deficit:
        lines.append("Бюджет в дефиците — платежи и расходы превышают доход.")
    else:
        surplus = r.free_money - total
        if surplus > 1000:
            lines.append(f"Резерв/накопления: {surplus:,.0f} ₽/мес")

    for cat, amt in sorted(r.budgets.items(), key=lambda x: CATEGORY_PRIORITY.get(x[0], 9)):
        strict_mark = " [зафиксировано]" if cat in r.strict_categories else ""
        lines.append(f"• {cat}: {amt:,.0f} ₽{strict_mark}")

    if r.skipped_categories:
        skipped = ", ".join(r.skipped_categories)
        lines.append(f"\nНе включено (нет бюджета): {skipped}")

    for note in r.notes:
        lines.append(f"\n{note}")

    return "\n".join(lines)


async def parse_user_spending_ai(text: str, groq_api_key: str = None) -> list[ParsedExpense]:
    """
    Парсинг трат через Groq — более точный чем regex.
    Возвращает список ParsedExpense.
    """
    if not groq_api_key:
        import os
        groq_api_key = os.getenv("GROQ_API_KEY")

    if not groq_api_key:
        return parse_user_spending(text)  # fallback на regex

    categories_list = list(_CATEGORY_KEYWORDS.keys()) + ["Прочее"]

    prompt = f"""Пользователь описывает свои ежемесячные траты:
"{text}"

Доступные категории: {", ".join(categories_list)}

Правила:
- Если пользователь называет конкретную сумму (например "танцы 6900", "связь 800") — это STRICT (нельзя срезать)
- Категория "Хобби" включает: танцы, спорт, спортзал, фитнес, секции, занятия, хобби
- Категория "Кредиты" включает: ипотека, кредит, займ, рассрочка — НЕ включай их в результат (они уже в обязательных платежах)
- Если сумма не указана — is_strict: false

Верни ТОЛЬКО JSON массив:
[{{"category": "...", "amount": число_или_0, "description": "...", "is_strict": true/false}}]

Если ничего не распознал: []"""

    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=groq_api_key)
        resp = await client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        import json
        items = json.loads(raw)
        return [
            ParsedExpense(
                category=it.get("category", "Прочее"),
                amount=float(it.get("amount", 0)),
                description=it.get("description", ""),
                is_strict=bool(it.get("is_strict", True)),
            )
            for it in items
            if isinstance(it, dict)
            and it.get("category", "Прочее") not in ("Кредиты", "Обязательные")
        ]
    except Exception as e:
        logging.error(f"parse_user_spending_ai error: {e}")
        return parse_user_spending(text)  # fallback
