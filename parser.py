"""Детерминированный разбор сообщений. Без ИИ — только правила.

Три типа сообщений:
1. Сверка баланса: «на карте 12000», «наличными 5000, на карте 12000»
2. Операции: «чай 500», «кофе 200 нал», «зп 30000», «кофе 200, такси 350»
3. Пересланное уведомление банка о чаевых: «Вам оставили чаевые: 350 ₽»
"""
import re

CASH = "cash"
CARD = "card"

# ─── суммы ───────────────────────────────────────────────────────────────────

_K_SUFFIX = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:тыс\.?|тысяч[аи]?|к)\b", re.IGNORECASE)
_CURRENCY = re.compile(r"(?<=\d)\s*(?:₽|руб(?:лей|ля|\.)?|р\.?)(?=\s|$|,)", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d ]*(?:[.,]\d{1,2})?")


def _normalize(text: str) -> str:
    """«30к» → «30000», «300 р» → «300», убирает валютные хвосты."""
    text = _K_SUFFIX.sub(lambda m: str(int(float(m.group(1).replace(",", ".")) * 1000)), text)
    text = _CURRENCY.sub("", text)
    return text


def extract_amount(text: str) -> float | None:
    """Первое число в тексте. «30 000», «1.5к», «250,50» — всё понимает."""
    m = _NUMBER.search(_normalize(text))
    if not m:
        return None
    raw = m.group(0).replace(" ", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0 or value > 100_000_000:
        return None
    return round(value, 2)


# ─── счёт ────────────────────────────────────────────────────────────────────

_CASH_WORDS = r"(?:наличными|наличкой|наличк[аи]|наличные|налом|нал|кэш(?:ем)?|в кармане)"
_CARD_WORDS = r"(?:на карте|на карту|картой|карта|на сч[её]те|на сч[её]т|безнал(?:ом)?)"

_CASH_RE = re.compile(_CASH_WORDS, re.IGNORECASE)
_CARD_RE = re.compile(_CARD_WORDS, re.IGNORECASE)


def detect_account(text: str) -> str | None:
    if _CASH_RE.search(text):
        return CASH
    if _CARD_RE.search(text):
        return CARD
    return None


# ─── сверка баланса ──────────────────────────────────────────────────────────

_RECON_PART = re.compile(
    rf"({_CASH_WORDS}|{_CARD_WORDS})\s*[:\-—]?\s*(\d[\d ]*(?:[.,]\d{{1,2}})?)\s*$",
    re.IGNORECASE,
)


def parse_reconciliation(text: str) -> dict | None:
    """«наличными 5000, на карте 12000» → {'cash': 5000.0, 'card': 12000.0}.

    Срабатывает только если КАЖДЫЙ фрагмент через запятую/точку с запятой/
    перенос строки — это «счёт + число» и ничего больше. Иначе это не сверка.
    """
    result: dict[str, float] = {}
    parts = re.split(r"[,;\n]+", _normalize(text).strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = _RECON_PART.fullmatch(part)
        if not m:
            return None
        account = CASH if _CASH_RE.fullmatch(m.group(1)) else CARD
        amount = float(m.group(2).replace(" ", "").replace(",", "."))
        if amount < 0 or amount > 100_000_000:
            return None
        result[account] = round(amount, 2)
    return result or None


# ─── тип и категория операции ────────────────────────────────────────────────

_INCOME_RULES = [
    (r"\bчаевы[ех]\b|\bчай\b", "Чаевые"),
    (r"\bзп\b|зарплат|аванс|оклад", "Зарплата"),
    (r"\bсмена\b|за смену|подработк|шабашк", "Подработка"),
    (r"преми|бонус", "Зарплата"),
    (r"подарил|подарок\s+\d|вернул[аи]?\s|долг\s+вернул", "Прочее"),
]

_EXPENSE_RULES = [
    (r"продукт|еда|обед|ужин|завтрак|кофе|перекус|столов|магаз|пятероч|магнит|вкусно", "Еда"),
    (r"такси|метро|автобус|проезд|бензин|заправк|каршер|самокат", "Транспорт"),
    (r"аренда|квартир|коммунал|жкх|свет|электрич", "Жильё"),
    (r"кино|бар|клуб|игр|развлеч|концерт", "Развлечения"),
    (r"аптек|врач|лекарств|стоматолог", "Здоровье"),
    (r"связь|телефон|интернет|подписк|мобильн", "Подписки"),
    (r"одежд|обувь|кроссовк|куртк", "Одежда"),
]


def classify(text: str) -> tuple[str, str]:
    """(kind, category): доход только по явным словам, иначе расход."""
    lower = text.lower()
    for pattern, category in _INCOME_RULES:
        if re.search(pattern, lower):
            return "income", category
    for pattern, category in _EXPENSE_RULES:
        if re.search(pattern, lower):
            return "expense", category
    return "expense", "Прочее"


def default_account(kind: str, category: str) -> str:
    """Чаевые руками вводят обычно наличные, зарплата и траты — карта."""
    if kind == "income" and category in ("Чаевые", "Подработка"):
        return CASH
    return CARD


# ─── операции ────────────────────────────────────────────────────────────────

def parse_transactions(text: str) -> list[dict]:
    """Разбирает одну или несколько операций через запятую/перенос строки.

    Возвращает [] если ни в одном фрагменте нет суммы.
    Каждая операция: {kind, account, amount, category, note}.
    """
    items = []
    for part in re.split(r"[,;\n]+", text):
        part = part.strip()
        if not part:
            continue
        amount = extract_amount(part)
        if amount is None:
            continue
        kind, category = classify(part)
        account = detect_account(part) or default_account(kind, category)
        items.append({
            "kind": kind,
            "account": account,
            "amount": amount,
            "category": category,
            "note": part,
        })
    return items


# ─── банковские уведомления (пересланные или скопированные) ──────────────────

# Точная форма: сумма стоит сразу после «Чаевые:» — только она защищает от
# соседних сумм в том же сообщении («Сумма заказа: 7690.00 р.»).
_BANK_TIPS_TIGHT = re.compile(
    r"чаевы[ех]?\s*[:\-—]?\s*(\d[\d ]*(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)
# Свободная форма: сумма до слова «чаевые» («Перевод 500 ₽ — чаевые от гостя»)
_BANK_TIPS_LOOSE = re.compile(
    r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|р\.?)?\D{0,20}?чаевы",
    re.IGNORECASE | re.DOTALL,
)

# Признаки, что это текст уведомления банка, а не ручной ввод пользователя
_BANK_SIGNATURE = re.compile(
    r"получен[ыо]\s+чаевые|вам\s+(?:оставили|перевели|отправили)\s+чаевые|"
    r"чаевы[ех]?\s*[:\-—]\s*\d",
    re.IGNORECASE,
)


def looks_like_bank_tips(text: str) -> bool:
    """Похоже ли сообщение на уведомление банка/сервиса чаевых."""
    return bool(_BANK_SIGNATURE.search(text))


# ─── расписание смен: «работаю 22 24 26», «смены 5, 7, 9» ────────────────────
# Триггер — только множественное «смены»/«работаю»/«график», чтобы не путать
# с доходом «смена 2500» (единственное число + сумма).
_SHIFT_SCHED_RE = re.compile(r"\b(смены|работа[ю]|график)\b", re.IGNORECASE)


def parse_shift_days(text: str, today) -> list | None:
    """«работаю 22 24 26» → [date(...), ...]. Число < сегодня → следующий месяц.

    Возвращает None, если это не команда расписания. `today` — datetime.date.
    """
    from datetime import date
    if not _SHIFT_SCHED_RE.search(text):
        return None
    days = [int(m) for m in re.findall(r"\d{1,2}", text)]
    days = sorted(set(d for d in days if 1 <= d <= 31))
    if not days:
        return None

    def make(year, month, day):
        try:
            return date(year, month, day)
        except ValueError:
            return None

    result = []
    for d in days:
        if d >= today.day:
            dt = make(today.year, today.month, d)
        else:  # день уже прошёл в этом месяце → следующий месяц
            ny = today.year + (1 if today.month == 12 else 0)
            nm = 1 if today.month == 12 else today.month + 1
            dt = make(ny, nm, d)
        if dt:
            result.append(dt)
    return result or None


_BANK_ORDER = re.compile(
    r"сумма\s+заказа\s*[:\-—]?\s*(\d[\d ]*(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)
_BANK_PERCENT = re.compile(r"\(\s*(\d{1,2}(?:[.,]\d)?)\s*%\s*\)")


def parse_bank_notification(text: str) -> dict | None:
    """Полный разбор банковского уведомления о чаевых.

    Возвращает {'amount', 'order_amount', 'tip_percent'} (последние два могут
    быть None). Процент берём из скобок «(7 %)», а если его нет — считаем сами
    из чека.
    """
    amount = parse_bank_tips(text)
    if amount is None:
        return None
    normalized = _normalize(text)

    order_amount = None
    m = _BANK_ORDER.search(normalized)
    if m:
        try:
            val = float(m.group(1).replace(" ", "").replace(",", "."))
            if 0 < val <= 10_000_000:
                order_amount = round(val, 2)
        except ValueError:
            pass

    tip_percent = None
    m = _BANK_PERCENT.search(text)
    if m:
        tip_percent = float(m.group(1).replace(",", "."))
    elif order_amount:
        tip_percent = round(amount / order_amount * 100, 1)
    if tip_percent is not None and not 0 < tip_percent <= 100:
        tip_percent = None

    return {"amount": amount, "order_amount": order_amount, "tip_percent": tip_percent}


def parse_bank_tips(text: str) -> float | None:
    """Ищет сумму чаевых в тексте банковского уведомления.

    Сначала точная форма «Чаевые: 500.00» (защита от других сумм в сообщении,
    например «Сумма заказа»), затем свободная «500 ₽ — чаевые».
    """
    normalized = _normalize(text)
    m = _BANK_TIPS_TIGHT.search(normalized)
    raw = m.group(1) if m else None
    if raw is None:
        m = _BANK_TIPS_LOOSE.search(normalized)
        raw = m.group(1) if m else None
    if raw is None:
        return None
    try:
        value = float(raw.replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    if value <= 0 or value > 1_000_000:
        return None
    return round(value, 2)
