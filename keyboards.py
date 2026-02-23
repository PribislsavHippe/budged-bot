from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ─── ГЛАВНОЕ МЕНЮ ────────────────────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Статистика"), KeyboardButton(text="Платежи")],
        [KeyboardButton(text="Бюджеты"),    KeyboardButton(text="Настройки")],
        [KeyboardButton(text="Доходы"),     KeyboardButton(text="Цели")],
        [KeyboardButton(text="История"),    KeyboardButton(text="ИИ-чат")],
    ], resize_keyboard=True)


# ─── КАТЕГОРИИ ────────────────────────────────────────────────────────────────

EXPENSE_CATEGORIES = [
    "Еда", "Кафе и рестораны", "Транспорт", "Жильё",
    "Развлечения", "Здоровье", "Одежда", "Связь",
    "Образование", "Кредиты", "Обязательные", "Прочее"
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


# ─── УНИВЕРСАЛЬНАЯ КНОПКА ОТМЕНЫ ─────────────────────────────────────────────

def cancel_kb(text: str = "Отмена") -> InlineKeyboardMarkup:
    """Одна кнопка «Отмена» — для любого шага ввода."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data="cancel")]
    ])


def skip_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопки «Пропустить» и «Отмена» — для необязательных шагов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Пропустить", callback_data="skip"),
            InlineKeyboardButton(text="Отмена",     callback_data="cancel"),
        ]
    ])


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
        [InlineKeyboardButton(text="Мои платежи",     callback_data="payment:list")],
        [InlineKeyboardButton(text="Добавить платёж", callback_data="payment:add")],
    ])


def payment_actions_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Оплачено", callback_data=f"payment:paid:{payment_id}"),
            InlineKeyboardButton(text="Удалить",  callback_data=f"payment:delete:{payment_id}"),
        ]
    ])


# ─── ДОХОДЫ — ЕДИНООБРАЗНОЕ МЕНЮ ─────────────────────────────────────────────

def income_menu_kb() -> InlineKeyboardMarkup:
    """Меню раздела Доходы — по образцу Платежей."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мои записи",         callback_data="planned_income:list")],
        [InlineKeyboardButton(text="Добавить доход/расход", callback_data="planned_income:add")],
    ])


def planned_income_actions_kb(income_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Удалить", callback_data=f"planned_income:delete:{income_id}")],
    ])

# Алиас для обратной совместимости
def planned_income_menu_kb() -> InlineKeyboardMarkup:
    return income_menu_kb()


# ─── ПОДТВЕРЖДЕНИЕ ТРАНЗАКЦИИ ─────────────────────────────────────────────────

def confirm_transaction_kb(confirm_cb: str, cancel_cb: str, edit_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Верно",      callback_data=confirm_cb),
            InlineKeyboardButton(text="Категория",  callback_data=edit_cb),
            InlineKeyboardButton(text="Нет",        callback_data=cancel_cb),
        ]
    ])


def delete_transaction_kb(transaction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Удалить", callback_data=f"tx:delete:{transaction_id}")]
    ])


# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

def settings_kb(has_google: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="Дни зарплаты",    callback_data="settings:salary_day")],
        [InlineKeyboardButton(text="Час напоминаний", callback_data="settings:reminder_hour")],
    ]
    if has_google:
        buttons.append([InlineKeyboardButton(text="Google Calendar подключён ✓", callback_data="settings:google_info")])
    else:
        buttons.append([InlineKeyboardButton(text="Подключить Google Calendar", callback_data="settings:google_connect")])
    buttons.append([InlineKeyboardButton(text="Пройти онбординг заново",    callback_data="settings:restart_onboarding")])
    buttons.append([InlineKeyboardButton(text="🗑 Удалить все мои данные",   callback_data="settings:reset_all_data")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── ЦЕЛИ ───────────────────────────────────────────────────────────────────

def goals_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мои цели",   callback_data="goal:list")],
        [InlineKeyboardButton(text="Новая цель", callback_data="goal:add")],
    ])


def goal_actions_kb(goal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Выставить напоминания", callback_data=f"goal:create_reminders:{goal_id}")],
        [InlineKeyboardButton(text="Цель достигнута",       callback_data=f"goal:done:{goal_id}")],
    ])
