from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ─── ГЛАВНОЕ МЕНЮ ────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💸 Добавить расход"), KeyboardButton(text="💰 Добавить доход")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📅 Платежи")],
        [KeyboardButton(text="🎯 Бюджеты"), KeyboardButton(text="⚙️ Настройки")],
    ], resize_keyboard=True)


# ─── КАТЕГОРИИ РАСХОДОВ ───────────────────────────────────

EXPENSE_CATEGORIES = [
    "🍕 Еда", "🚗 Транспорт", "🏠 Жильё", "🎮 Развлечения",
    "💊 Здоровье", "👕 Одежда", "📱 Связь", "📚 Образование",
    "💳 Обязательные", "🛒 Прочее"
]

INCOME_CATEGORIES = [
    "💼 Зарплата", "💰 Аванс", "💻 Фриланс", "🎁 Подарок", "📈 Прочее"
]


def expense_categories_kb() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for i, cat in enumerate(EXPENSE_CATEGORIES):
        row.append(InlineKeyboardButton(text=cat, callback_data=f"cat_exp:{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def income_categories_kb() -> InlineKeyboardMarkup:
    buttons = []
    for cat in INCOME_CATEGORIES:
        buttons.append([InlineKeyboardButton(text=cat, callback_data=f"cat_inc:{cat}")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── СТАТИСТИКА ───────────────────────────────────────────

def stats_period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 За неделю", callback_data="stats:week"),
            InlineKeyboardButton(text="📆 За месяц", callback_data="stats:month"),
        ],
        [InlineKeyboardButton(text="📋 Все транзакции", callback_data="stats:all")]
    ])


# ─── ПЛАТЕЖИ ─────────────────────────────────────────────

def payments_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить платёж", callback_data="payment:add")],
        [InlineKeyboardButton(text="📋 Мои платежи", callback_data="payment:list")],
    ])


def payment_actions_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Оплачено", callback_data=f"payment:paid:{payment_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"payment:delete:{payment_id}"),
        ]
    ])


# ─── ПОДТВЕРЖДЕНИЕ ───────────────────────────────────────

def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=f"confirm:{action}"),
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel"),
        ]
    ])


# ─── НАСТРОЙКИ ───────────────────────────────────────────

def settings_kb(has_google: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📅 День зарплаты", callback_data="settings:salary_day")],
        [InlineKeyboardButton(text="🔔 Час напоминаний", callback_data="settings:reminder_hour")],
    ]
    if has_google:
        buttons.append([InlineKeyboardButton(text="✅ Google Calendar подключён", callback_data="settings:google_disconnect")])
    else:
        buttons.append([InlineKeyboardButton(text="📅 Подключить Google Calendar", callback_data="settings:google_connect")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
