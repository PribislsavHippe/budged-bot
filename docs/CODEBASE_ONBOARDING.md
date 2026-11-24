# Карта проекта «На стаффе»

## Что это

«На стаффе» состоит из Telegram-бота, миниаппа «Статистика» и кабинета ресторана. Официанты пользуются ими для графика, выходных, часов и чаевых. Менеджеры составляют график и публикуют смены. Обзор функций и порядок установки приведены в [README](../README.md).

## Технологии и запуск

| Слой | Что используется |
| --- | --- |
| Сервер | Python 3.11.9, aiogram 3.31, aiohttp 3.14, APScheduler 3.10 |
| Данные | Supabase через Python-клиент; схема `schema.sql`, последовательные `migration_v*.sql` |
| Интерфейс | Telegram Mini App: HTML, CSS и JavaScript в `webapp/`, без сборщика |
| Внешние сервисы | Календарь по ссылке ICS (`calendar_feed.py`), необязательное распознавание фото через Groq |
| Развёртывание | Render через `render.yaml`: один веб-сервис, `python main.py` |

Локально `main.py` запускает Telegram polling. При заданном `WEBHOOK_HOST` тот же процесс поднимает aiohttp, Telegram webhook, `/app` и API. Обязательные переменные перечислены в `env.example`; ключ Supabase должен оставаться только на сервере. Проверки запускает GitHub Actions: `.github/workflows/checks.yml` вызывает `tests/run_checks.py`.

## Карта исполнения

```text
Telegram → main.py → aiogram routers → handlers.py / schedule_chat.py / report_photo.py / identity_chat.py
                                      ↓                     ↓
                               parser.py, workday.py     доменные модули
                                      ↓                     ↓
                                      db.py / sales_db.py → Supabase

Telegram Mini App → webapp/index.html + webapp/*.js → webapp_api.py → доменные модули → Supabase
                                                     ↘ sales_api.py / restaurant_api.py / research_api.py

jobs.py → напоминания о смене, запрос конца смены, уведомления о категориях, очистка
```

`main.py` задаёт порядок роутеров; общий обработчик текста в `handlers.py` стоит последним. В `legacy/` лежит прежняя версия, которая не подключена к текущему запуску.

## Один полный путь: запись чаевых

1. Человек отправляет «чай 500». `main.py` передаёт сообщение в aiogram; `handlers.handle_text` получает Telegram ID.
2. `parser.parse_transactions` определяет сумму, тип и счёт по правилам, без языковой модели. `workday.py` задаёт операционный день: по умолчанию он начинается в 06:00 МСК.
3. `db.add_entries` сохраняет запись в `entries` с `user_id` и `source_key`, чтобы повторная доставка того же сообщения не создала дубликат.
4. Бот отвечает сохранённой суммой и кнопкой отмены. Статистика пересчитывается из записей; отдельный «баланс» не хранится.

Для мини-приложения граница другая: `webapp_api._auth` проверяет подписанное Telegram `initData`; операции в БД выполняются в области проверенного пользователя. В приватном режиме денежные записи хранятся на устройстве, поэтому перед изменением этого пути нужно отдельно читать `webapp/private.js` и API приватности.

## Где искать нужный сценарий

| Задача | Сначала открыть |
| --- | --- |
| Сообщения и кнопки бота | `handlers.py`, затем профильный `*_chat.py` |
| Разбор свободного текста | `parser.py`, `sales_chat.py`, `chat_dates.py` |
| Личный график и рабочее время | `schedule_chat.py`, `schedule.py`, `schedule_sheet.py` |
| Составление и публикация графика | `roster_api.py`, `roster.py`, `webapp/roster-editor.js` |
| Сбор выходных и часов доступности | `roster_wishes.py`, `days_off_app.py`, `days_off_widget.py`, `roster_time_store.py` |
| Тренинги и меню | `trainings.py`, `menu_chat.py`, `menu_store.py` |
| Фото отчёта | `report_photo.py`, `report_vision.py` |
| Статистика и история в приложении | `webapp/index.html`, `webapp/tips.js`, `webapp_api.py`, `stats.py` |
| Ресторан и права | `identity_chat.py`, `identity.py`, `restaurant_api.py` |
| Данные и миграции | `db.py`, `schema.sql`, `migration_v*.sql` |
| Фоновые задачи и календари | `jobs.py`, `calendar_feed.py` |
| Исследовательские события | `research.py`, `research_api.py`, `research_stats.py` |

## Правила работы и проверки

- Начни с [продуктовых принципов](PRODUCT_PRINCIPLES.md) и [инженерного цикла](ENGINEERING_WORKFLOW.md); корневой `AGENTS.md` указывает, когда решение нужно согласовать с владельцем.
- Код в основном состоит из модулей `snake_case`; асинхронные обработчики aiogram и aiohttp вызывают синхронный клиент Supabase через `db._execute`. Ошибки внешних сервисов пишутся в диагностику, а человеку показывается безопасное сообщение.
- Тесты находятся в `tests/`: `test_*.py` запускаются через `unittest discover`, а отдельные `*_checks.py` — как скрипты. HTTP и внешние сервисы в основных проверках замоканы.
- Локальный старт: `.venv/bin/python main.py` после настройки окружения. Основной набор тестов: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`; для изменённого сценария добавь соответствующий `tests/*_checks.py`.
- Недавние коммиты в основном имеют короткие английские описания изменений, есть и русские. Обязательный порядок веток и PR по доступным файлам не установлен.
- Запросы к Groq из `report_vision.chat_json` используют общий ограничитель частоты. Настройки и обработку ответа 429 смотри в коде.

## Что сверять перед изменениями

Порядок миграций для новой базы описан в [инструкции установки](../README.md#установка). Перед обновлением рабочей базы сверяй, какие миграции уже применены. Интеграции Telegram, Supabase, Google и Render проверяются отдельно после развёртывания.
