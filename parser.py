"""Детерминированный разбор сообщений. Без ИИ — только правила.

Три типа сообщений:
1. Сверка баланса: «на карте 12000», «наличными 5000, на карте 12000»
2. Операции: «чай 500», «кофе 200 нал», «зп 30000», «кофе 200, такси 350»
3. Пересланное уведомление банка о чаевых: «Вам оставили чаевые: 350 ₽»
"""
import re
from decimal import Decimal, InvalidOperation

CASH = "cash"
CARD = "card"

# ─── суммы ───────────────────────────────────────────────────────────────────

# «30к», «30 тыс» — тысячи. Отдельно стоящее «к» — тысячи, только если за ним конец записи, знак
# препинания, валюта или счёт («зп 30 к», «30 к нал»); иначе это предлог: «500 к зарплате», «такси 300 к дому».
_K_SUFFIX = re.compile(
    r"(\d+(?:[.,]\d+)?)(?:\s*(?:тыс\.?|тысяч[аи]?)|к|\s+к(?=\s*(?:$|[,;\n]|₽|руб|р\b|р\.|нал|кэш|карт|безнал)))(?!\w)",
    re.IGNORECASE)
_CURRENCY = re.compile(r"(?<=\d)\s*(?:₽|руб(?:лей|ля|\.)?|р\.?)(?=\s|$|,)", re.IGNORECASE)
_NUMBER = re.compile(r"[-−+]?\d[\d ]*(?:[.,]\d+)?")


class AmountFormatError(ValueError):
    """An ambiguous amount must not silently become a different operation."""


_THOUSANDS = re.compile(r"\d{1,3}(?: \d{3})+")


def _integer_part(token: str) -> str:
    return re.split(r"[.,]", token.strip().lstrip("-−+").strip(), maxsplit=1)[0]


def _bad_grouping(token: str) -> bool:
    """Пробел внутри числа — разделитель тысяч только в виде «30 000», «1 250 000».

    «500 35» не похоже ни на одно число, поэтому такую запись не угадываем.
    """
    integer = _integer_part(token)
    return " " in integer and not _THOUSANDS.fullmatch(integer)


def _two_amounts_lookalike(token: str) -> bool:
    """«500 350» читается и как 500 350, и как две суммы.

    Для чаевых, которые не бывают шестизначными, это почти всегда две суммы.
    """
    groups = _integer_part(token).split(" ")
    return len(groups) > 1 and len(groups[0]) == 3 and groups[-1] != "000"


def _checked_number(match: re.Match, text: str) -> Decimal:
    raw = match.group().strip().replace(" ", "").replace(",", ".")
    if _bad_grouping(match.group()):
        raise AmountFormatError("amount_format")
    if match.start()>0 and text[match.start()-1] in '.,':
        raise AmountFormatError("amount_format")
    if raw.startswith(("-", "−")) or ("." in raw and len(raw.rsplit(".", 1)[1]) > 2):
        raise AmountFormatError("amount_format")
    if re.match(r"[.,]\d", text[match.end():]):
        raise AmountFormatError("amount_format")
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise AmountFormatError("amount_format") from None


def _normalize(text: str) -> str:
    """«30к» → «30000», «300 р» → «300», убирает валютные хвосты."""
    text = text.replace('\u00a0',' ').replace('\u202f',' ')
    text = _K_SUFFIX.sub(lambda m: format(Decimal(m.group(1).replace(",", ".")) * 1000, 'f'), text)
    text = _CURRENCY.sub("", text)
    return text


def extract_amount(text: str) -> float | None:
    """Первое число в тексте. «30 000», «1.5к», «250,50» — всё понимает."""
    normalized = _normalize(text)
    m = _NUMBER.search(normalized)
    if not m:
        return None
    value = _checked_number(m, normalized)
    if value <= 0 or value > 10_000_000:
        return None
    return float(value)


# ─── счёт ────────────────────────────────────────────────────────────────────

_CASH_WORDS = r"(?:\b(?:наличными|наличкой|наличк[аи]|наличные|налом|нал|кэш(?:ем)?)\b|в кармане)"
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
    # «гость оставил 500», «оставили 1000 на карту», «моя доля 700» — тоже чаевые (аудит фраз 20.11.2026).
    (r"\bоставил\w*|\bдол[яюиь]\b", "Чаевые"),
    (r"\bзп\b|зарплат|аванс|оклад", "Зарплата"),
    (r"\bсмена\b|подработк|шабашк", "Подработка"),
    (r"преми|бонус", "Зарплата"),
    (r"подарил|подарок\s+\d|вернул[аи]?\s|долг\s+вернул", "Прочее"),
]

# Категории расходов — как в приложении: Мойка, Бар, Еда, Такси (handlers.SHIFT_SPEND_CATEGORIES); остальное — «Прочее».
# «Бар 500», «Мойка 100» в конце смены — доля чаевых бару и мойке (20.11.2026).
# Каждое правило совпадает с началом слова: «среда» — не «еда», «барбершоп» — не «бар».
_EXPENSE_RULES = [
    (r"\b(?:бар(?:у|е|а|ы|ов)?|бармен\w*)\b", "Бар"),
    (r"\b(?:мойк\w*|мойщ\w*|посудомо\w*)", "Мойка"),
    (r"\b(?:продукт|ед[аыуе]\b|обед|ужин|завтрак|кофе|перекус|столов(?:ая|ой|ую|к[аиуе])\b|магаз|пятероч|магнит|вкусно)", "Еда"),
    (r"\b(?:такси|каршер)", "Такси"),
]
# Понятные траты вне этих категорий: записываются в «Прочее» без вопроса.
_OTHER_EXPENSE = re.compile(
    r"\b(?:метро|автобус|проезд|бензин|заправк|самокат|аренд|квартир|коммунал|жкх|свет\b|электрич|кино\b|клуб(?:е|а|ы|ов)?\b|"
    r"игр(?:а|ы|у|е|ушк\w*)?\b|развлеч|концерт|аптек|врач|лекарств|стоматолог|связь|телефон|интернет|подписк|мобильн|"
    r"одежд|обувь|кроссовк|куртк)", re.IGNORECASE)


def classify(text: str) -> tuple[str, str]:
    """(kind, category): сумма без пояснения — чаевые, иначе явные слова или расход."""
    lower = _normalize(text.lower())
    if re.fullmatch(r"\s*\d[\d ]*(?:[.,]\d{1,2})?\s*", lower):
        return "income", "Чаевые"
    for pattern, category in _INCOME_RULES:
        if re.search(pattern, lower):
            return "income", category
    if re.search(r"за смену", lower) and re.search(r"получил|заработал|выплатил|заплатили|дали\b|пришл", lower):
        return "income", "Подработка"
    for pattern, category in _EXPENSE_RULES:
        if re.search(pattern, lower):
            # «бармен дал 500», «кухня скинула 300» — деньги пришли от коллег: не угадывать, спросить.
            if category in ("Бар", "Мойка") and re.search(r"\b(?:дал[аи]?|скинул\w*|перев[её]л\w*|вернул\w*)\b", lower):
                return "expense", "Прочее"
            return "expense", category
    # «за смену» без слова расхода — оплата смены; «такси 350 за смену» — расход.
    if re.search(r"за смену", lower) and not re.search(_GENERAL_EXPENSE, lower):
        return "income", "Подработка"
    return "expense", "Прочее"


_GENERAL_EXPENSE = r"расход|трат|потрат|заплатил|штраф|вычл|списал|удерж|оплатил|купил"


def default_account(kind: str, category: str) -> str:
    """Чаевые руками вводят обычно наличные, зарплата и траты — карта."""
    if kind == "income" and category in ("Чаевые", "Подработка"):
        return CASH
    if category in ("Бар", "Мойка"):  # отдают из наличных чаевых, как трата смены в приложении
        return CASH
    return CARD


# ─── операции ────────────────────────────────────────────────────────────────

# Числа в пояснении к записи — не суммы: номер стола, время, число гостей, год.
_NOT_AMOUNTS = [re.compile(p, re.IGNORECASE) for p in (
    r"(?:\bстол\w*|\bст\.|№|#)\s*№?\s*\d+(?:\s*(?:и|,)\s*\d+)*",
    r"\b\d{1,2}:\d{2}\b",
    r"\b\d{1,2}\.\d{1,2}\.\d{4}\b",
    r"\d+\s*(?:шт|штук|чел|человек|гост|персон)\w*",
    r"\b(?:на|для)\s+\d{1,2}\s+(?:человек|гост|персон)\w*",
)]
_YEAR = re.compile(r"\b20\d{2}\b")


def _amount_text(part: str) -> str:
    """Фрагмент без пояснений с числами; если их вычеркнуть нечем — как есть."""
    text = _normalize(part)
    stripped = text
    for pattern in _NOT_AMOUNTS:
        stripped = pattern.sub(" ", stripped)
    if len(_NUMBER.findall(stripped)) > 1:
        stripped = _YEAR.sub(" ", stripped)
    # Во фрагменте только номер стола («стол 5 и 6») — суммы в нём нет; откат к тексту целиком сделал бы
    # номера столов суммами. Для остальных пояснений откат прежний.
    if not _NUMBER.search(stripped) and _NOT_AMOUNTS[0].search(text):
        return stripped
    return stripped if _NUMBER.search(stripped) else text

# Дата записи в тексте: «вчера чай 800», «за 17 ноября 900», «за 17.11 900». Без даты — день сообщения.
# Голое «17.11» датой не считается: так пишут и время, и копейки. Решение — в handlers (day → work_date).
_MONTHS = ('январ', 'феврал', 'март', 'апрел', 'ма', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр')
_DAY_WORDS = re.compile(r"(?:\bза\s+)?\b(позавчера|вчера|сегодня)\b", re.IGNORECASE)
# Месяц — целым словом: «5 мартини», «соус 5 майонез», «12 июльских» — не даты.
_DAY_MONTH = re.compile(r"(?:\bза\s+)?\b(\d{1,2})\s+(январ[ья]|феврал[ья]|марта?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|"
                        r"августа?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])(?![а-яё])", re.IGNORECASE)
_DAY_DOTTED = re.compile(r"\bза\s+(\d{1,2})\.(\d{1,2})\b(?!\.\d)", re.IGNORECASE)
# «чай 500 и такси 300», «кофе 200 плюс такси 350» — две записи, если по обе стороны есть число.
_AND = re.compile(r"\s+(?:и|плюс)\s+", re.IGNORECASE)
_SHARE = re.compile(r"\bдол[яюиь]\b", re.IGNORECASE)
_TOTAL = re.compile(r"на\s+всех|\bобщ|\bвсего\b|на\s+(?:двоих|троих|компанию|смену)", re.IGNORECASE)


def take_date(part: str) -> tuple[str, tuple | None]:
    """Вынимает дату записи из фрагмента: (текст без даты, ('offset', -1) | ('date', день, месяц) | None)."""
    m = _DAY_WORDS.search(part)
    if m:
        offset = {'сегодня': 0, 'вчера': -1, 'позавчера': -2}[m.group(1).lower()]
        return (part[:m.start()] + ' ' + part[m.end():]).strip(), ('offset', offset)
    m = _DAY_MONTH.search(part)
    if m:
        stem = m.group(2).lower()
        month = next(i for i, name in enumerate(_MONTHS, 1) if stem.startswith(name))
        return (part[:m.start()] + ' ' + part[m.end():]).strip(), ('date', int(m.group(1)), month)
    m = _DAY_DOTTED.search(part)
    if m and 1 <= int(m.group(2)) <= 12:
        return (part[:m.start()] + ' ' + part[m.end():]).strip(), ('date', int(m.group(1)), int(m.group(2)))
    return part, None


def _pieces(part: str) -> list[str]:
    """«Чай 500 и мойка 50» — две записи. «Стол 5 и 6» — номер столов."""
    pieces = _AND.split(part)
    money = re.compile(r"\d{3}|\d\s*к\b|\d\s*тыс", re.IGNORECASE)
    if len(pieces) == 1:
        return pieces
    for piece in pieces:
        if money.search(piece):
            continue
        kind, category = classify(piece)
        if not explicit_expense({'kind': kind, 'category': category, 'note': piece}):
            return [part]
    return pieces


def parse_transactions(text: str) -> list[dict]:
    """Разбирает одну или несколько операций через запятую/перенос строки.

    Возвращает [] если ни в одном фрагменте нет суммы.
    Каждая операция: {kind, account, amount, category, note}.
    """
    # Validate before splitting: 123,456 is ambiguous, not two operations.
    normalized = _normalize(text)
    for match in _NUMBER.finditer(normalized):
        _checked_number(match, normalized)
    items = []
    # Запятая между цифрами с одной/двумя цифрами после неё — копейки.
    # Остальные запятые, точка с запятой и перенос строки разделяют записи.
    parts = [x for chunk in re.split(r"[;\n]+|(?<!\d),|,(?!\d{1,2}(?:\D|$))", text) for x in _pieces(chunk)]
    for part in parts:
        part = part.strip()
        if not part:
            continue
        note = part
        part, day = take_date(part)
        counted = _amount_text(part)
        # Две суммы в одном фрагменте: вторая молча пропала бы, а первая записалась бы.
        if len(_NUMBER.findall(counted)) > 1:
            raise AmountFormatError("several_amounts")
        amount = extract_amount(counted)
        if amount is None:
            continue
        kind, category = classify(part)
        if category == "Чаевые" and any(_two_amounts_lookalike(m.group()) for m in _NUMBER.finditer(counted)):
            raise AmountFormatError("amount_format")
        account = detect_account(part) or default_account(kind, category)
        item = {
            "kind": kind,
            "account": account,
            "amount": amount,
            "category": category,
            "note": note,
        }
        if day is not None:
            item["day"] = day
        items.append(item)
    # «чай 3500 на всех, моя доля 700»: записывается своя доля, общая сумма — нет.
    if any(i["category"] == "Чаевые" and _SHARE.search(i["note"]) for i in items):
        items = [i for i in items if not (i["category"] == "Чаевые" and _TOTAL.search(i["note"])
                                          and not _SHARE.search(i["note"]))]
    return items


# Слова, при которых расход «Прочее» записывается без вопроса: человек сам назвал это тратой.
GENERAL_EXPENSE = re.compile(_GENERAL_EXPENSE, re.IGNORECASE)


def uncertain(item: dict) -> bool:
    """Сумма с незнакомыми словами («гостей было 150», «чек 2300», «на карту 300»): спросить, а не угадывать."""
    return (item["kind"] == "expense" and item["category"] == "Прочее" and not GENERAL_EXPENSE.search(item["note"])
            and not _OTHER_EXPENSE.search(_normalize(item["note"].lower())))


_EXPENSE_TIME = re.compile(r"\b(?:в|во|с|до|к|после|около)\s+\d{1,2}(?=\b)", re.IGNORECASE)


def explicit_expense(item: dict) -> bool:
    """Разрешает небольшую трату с понятным назначением, сохраняя защиту для «такси в 23»."""
    if item['kind'] != 'expense' or uncertain(item):
        return False
    part, _ = take_date(item['note'])
    counted = _amount_text(part)
    return (extract_amount(counted) is not None and '%' not in counted and not _EXPENSE_TIME.search(counted)
            and not any(pattern.search(counted) for pattern in _NOT_AMOUNTS))


# Есть ли в тексте признаки денег: без них «сумма неоднозначна» — неуместный ответ («с 11 до 23»).
def money_hint(text: str) -> bool:
    lower = text.lower()
    if detect_account(text) or re.search(r"₽|руб|\d\s*к\b|тыс", lower) or GENERAL_EXPENSE.search(lower):
        return True
    return bool(_OTHER_EXPENSE.search(lower)) or any(re.search(pattern, lower) for pattern, _ in _INCOME_RULES + _EXPENSE_RULES)


# ─── банковские уведомления (пересланные или скопированные) ──────────────────

# Точная форма: сумма стоит сразу после «Чаевые:» — только она защищает от
# соседних сумм в том же сообщении («Сумма заказа: 7690.00 р.»).
_BANK_TIPS_TIGHT = re.compile(
    r"чаевы[ех]?\s*[:—]?\s*([-−+]?\d[\d ]*(?:[.,]\d+)?)(?![.,:]?\d)",
    re.IGNORECASE,
)
# Свободная форма: сумма до слова «чаевые» («Перевод 500 ₽ — чаевые от гостя»)
_BANK_TIPS_LOOSE = re.compile(
    r"([-−+]?\d[\d ]*(?:[.,]\d+)?)\s*(?:₽|руб\.?|р\.?)?\D{0,20}?чаевы",
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


# Слова, которые могут стоять внутри перечисления дней и его не обрывают:
# «работаю 1 числа и 5 го».
_DAY_FILLER = {"и", "числа", "число", "го", "е"}


def _days_after_trigger(text: str) -> list[int]:
    """Числа из перечисления сразу после слова-триггера.

    Раньше числа выскребались из всего сообщения подряд, и «работаю сегодня,
    потратил 200 на такси» ставило смену на 20-е: регулярка вырезала «20»
    из середины «200». Теперь читаем только непрерывный список дней после
    триггера и останавливаемся на первом постороннем слове — всё, что дальше,
    к расписанию не относится.
    """
    for m in _SHIFT_SCHED_RE.finditer(text):
        days = []
        for token in re.findall(r"[^\s,;:.—-]+", text[m.end():]):
            if token.isdigit():
                days.append(int(token))
                continue
            if token.lower() in _DAY_FILLER:
                continue
            break
        days = sorted({d for d in days if 1 <= d <= 31})
        if days:
            return days
    return []


def shift_day_problems(text: str, today) -> list[int]:
    """Числа после «работаю»/«смены»/«график», которые не могут быть днём: «работаю 31» в ноябре, «работаю 5 500».

    Раньше такое сообщение молча теряло число или становилось расходом 31 ₽ (ревью 15.11.2026).
    Пустой список — всё в порядке или это не про смены."""
    from datetime import date
    bad = []
    for m in _SHIFT_SCHED_RE.finditer(text):
        numbers = []
        for token in re.findall(r"[^\s,;:.—-]+", text[m.end():]):
            if token.isdigit():
                numbers.append(int(token))
                continue
            if token.lower() in _DAY_FILLER:
                continue
            break
        for d in numbers:
            month_year = (today.year, today.month) if d >= today.day else (
                today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1)
            try:
                date(month_year[0], month_year[1], d)
            except ValueError:
                bad.append(d)
        if numbers:
            return bad
    return bad


def parse_shift_days(text: str, today) -> list | None:
    """«работаю 22 24 26» → [date(...), ...]. Число < сегодня → следующий месяц.

    Возвращает None, если это не команда расписания. `today` — datetime.date.
    """
    from datetime import date
    if not _SHIFT_SCHED_RE.search(text):
        return None
    days = _days_after_trigger(text)
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
    r"сумма\s+заказа\s*[:\-—]?\s*([-−+]?\d[\d ]*(?:[.,]\d+)?)",
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
            order_amount = extract_amount(m.group(1))
        except AmountFormatError:
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
        value = extract_amount(raw)
    except AmountFormatError:
        return None
    if value is None or value <= 0 or value > 1_000_000:
        return None
    return round(value, 2)
