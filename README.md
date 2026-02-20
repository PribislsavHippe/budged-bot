# 💰 Budget Bot — Telegram бот для ведения бюджета

## Что умеет бот

- 💸 **Учёт расходов** по категориям (еда, транспорт, жильё и т.д.)
- 💰 **Учёт доходов** с напоминанием в день зарплаты
- 📅 **Обязательные платежи** — бот напомнит за 2 дня
- 🎯 **Бюджеты** — лимиты по категориям с предупреждениями
- 📊 **Статистика** за неделю и месяц с разбивкой по категориям
- 📆 **Google Calendar** — автоматически создаёт события для платежей
- 💪 **Мотивация** — ежедневные напоминания, еженедельные и месячные отчёты

---

## Установка и запуск

### Шаг 1 — Создай бота в Telegram

1. Открой [@BotFather](https://t.me/BotFather)
2. Напиши `/newbot` и следуй инструкциям
3. Скопируй **Bot Token**

---

### Шаг 2 — Настрой Supabase

1. Зарегистрируйся на [supabase.com](https://supabase.com)
2. Создай новый проект
3. Открой **SQL Editor** и выполни содержимое файла `schema.sql`
4. Скопируй из **Settings → API**:
   - `Project URL` → это `SUPABASE_URL`
   - `anon public` ключ → это `SUPABASE_KEY`

---

### Шаг 3 — Настрой Google Calendar API (опционально)

1. Открой [Google Cloud Console](https://console.cloud.google.com)
2. Создай новый проект
3. Включи **Google Calendar API** (APIs & Services → Enable APIs)
4. Создай **OAuth 2.0 Client ID** (APIs & Services → Credentials → Create Credentials → OAuth client ID)
   - Тип приложения: **Web application**
   - Authorized redirect URIs: `https://твой-домен.onrender.com/google/callback`
5. Скопируй Client ID и Client Secret

> ⚠️ Если не нужен Google Calendar — просто не заполняй Google-переменные. Остальное будет работать.

---

### Шаг 4 — Деплой на Render

1. Загрузи код на GitHub (все файлы из этого проекта)
2. Открой [render.com](https://render.com) и создай аккаунт
3. New → Web Service → Connect your GitHub repo
4. Render сам найдёт `render.yaml` и настроит сервис
5. В разделе **Environment Variables** добавь все переменные из `.env.example`
6. После деплоя скопируй URL вида `https://budget-bot-xxxx.onrender.com`
7. Вставь этот URL как `WEBHOOK_HOST` в переменные окружения
8. Передеплой сервис

---

### Шаг 5 — Локальный запуск (для тестирования)

```bash
# Клонируй репозиторий
git clone <твой-репозиторий>
cd budget_bot

# Установи зависимости
pip install -r requirements.txt

# Скопируй и заполни .env
cp .env.example .env

# Для локального тестирования используй polling вместо webhook
# Измени в main.py: добавь режим polling
python main.py
```

> 💡 Для локальной разработки удобнее использовать polling. Замени в `main.py` функцию `main()` на использование `dp.start_polling(bot)`.

---

## Структура проекта

```
budget_bot/
├── main.py                    # Точка входа, webhook сервер
├── requirements.txt           # Зависимости
├── render.yaml                # Конфигурация Render
├── schema.sql                 # SQL схема для Supabase
├── .env.example               # Пример переменных окружения
├── database/
│   └── db.py                  # Все запросы к Supabase
├── handlers/
│   ├── start.py               # /start, настройки
│   ├── transactions.py        # Расходы, доходы, статистика
│   ├── payments.py            # Обязательные платежи
│   └── budget.py              # Бюджеты по категориям
├── scheduler/
│   └── jobs.py                # Все напоминания и отчёты
├── services/
│   └── google_calendar.py     # Google Calendar API
└── utils/
    └── keyboards.py           # Все клавиатуры
```

---

## Расписание напоминаний

| Триггер | Что происходит |
|---------|---------------|
| Каждый час | Проверка: кому сейчас напомнить о расходах |
| 09:00 | Напоминания об обязательных платежах за 1-3 дня |
| 10:00 | Напоминание внести доход в день зарплаты |
| 12:00 | Проверка бюджетов (предупреждение при 80%/100%) |
| Воскресенье 18:00 | Еженедельный отчёт |
| 1-е число 10:00 | Месячный отчёт |

---

## Переменные окружения

| Переменная | Описание |
|-----------|---------|
| `BOT_TOKEN` | Токен от @BotFather |
| `SUPABASE_URL` | URL проекта Supabase |
| `SUPABASE_KEY` | Anon key Supabase |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `GOOGLE_REDIRECT_URI` | `https://твой-домен.onrender.com/google/callback` |
| `WEBHOOK_HOST` | `https://твой-домен.onrender.com` |
| `WEBHOOK_PATH` | `/webhook` (по умолчанию) |
| `WEBAPP_HOST` | `0.0.0.0` |
| `WEBAPP_PORT` | `8080` |
