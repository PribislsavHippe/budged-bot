from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ─── ГЛАВНОЕ МЕНЮ (упрощённое) ───────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Статистика"), KeyboardButton(text="Платежи")],
        [KeyboardButton(text="Бюджеты"),   KeyboardButton(text="Настройки")],
    ], resize_keyboard=True)


# ─── КАТЕГОРИИ ────────────────────────────────────────────────────────────────

EXPENSE_CATEGORIES = [
    "Еда", "Транспорт", "Жильё", "Развлечения",
    "Здоровье", "Одежда", "Связь", "Образование",
    "Обязательные", "Прочее"
]

INCOME_CATEGORIES = [
    "Зарплата", "Оплата за неделю", "Аванс", "Частичная оплата",
    "Фриланс", "Подработка", "Подарок", "Инвестиции", "Прочее"
]


def expense_categories_kb(prefix: str = "cat_exp") -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for cat in EXPENSE_CATEGORIES:
        row.append(InlineKeyboardButton(text=cat, callback_data=f"{prefix}:{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def income_categories_kb(prefix: str = "cat_inc") -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for cat in INCOME_CATEGORIES:
        row.append(InlineKeyboardButton(text=cat, callback_data=f"{prefix}:{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── СТАТИСТИКА ───────────────────────────────────────────────────────────────

def stats_period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="За неделю", callback_data="stats:week"),
            InlineKeyboardButton(text="За месяц",  callback_data="stats:month"),
        ],
        [InlineKeyboardButton(text="Всё время", callback_data="stats:all")]
    ])


# ─── ПЛАТЕЖИ ─────────────────────────────────────────────────────────────────

def payments_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить платёж", callback_data="payment:add")],
        [InlineKeyboardButton(text="Мои платежи",     callback_data="payment:list")],
    ])


def payment_actions_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Оплачено", callback_data=f"payment:paid:{payment_id}"),
            InlineKeyboardButton(text="Удалить",  callback_data=f"payment:delete:{payment_id}"),
        ]
    ])


# ─── ПОДТВЕРЖДЕНИЕ ТРАНЗАКЦИИ ─────────────────────────────────────────────────

def confirm_transaction_kb(confirm_cb: str, cancel_cb: str, edit_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Верно", callback_data=confirm_cb),
            InlineKeyboardButton(text="Категория", callback_data=edit_cb),
            InlineKeyboardButton(text="Нет", callback_data=cancel_cb),
        ]
    ])


# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

def settings_kb(has_google: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Дни зарплаты",    callback_data="settings:salary_day")],
        [InlineKeyboardButton(text="Час напоминаний", callback_data="settings:reminder_hour")],
    ]
    if has_google:
        buttons.append([InlineKeyboardButton(text="Google Calendar подключён", callback_data="settings:google_disconnect")])
    else:
        buttons.append([InlineKeyboardButton(text="Подключить Google Calendar", callback_data="settings:google_connect")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
