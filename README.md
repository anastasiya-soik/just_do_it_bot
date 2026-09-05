# 🎯 just do it — telegram habit tracker

![Python](https://img.shields.io/badge/Python-3.13-blue)
![aiogram](https://img.shields.io/badge/aiogram-3.x-green)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red)
![Deployed](https://img.shields.io/badge/Deployed-Railway-purple)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

> Telegram-бот для отказа от вредных привычек: сахар, фастфуд, алкоголь, никотин, шортсы — или любой свой челлендж.

**Бот:** [t.me/just_never_do_it_bot](https://t.me/just_never_do_it_bot)

---

## Возможности

- **ежедневный чек** — отдельное сообщение на каждый челлендж с кнопками «победа ✅» / «срыв 😔»
- **заморозки** — спасают стрик при срыве. копятся автоматически за стрики 7/14/30/60/100 дней, можно купить за ⭐️
- **AI-коуч** — google gemini генерирует короткие живые ответы: при победах, срывах, в итогах недели. очередь запросов защищает от спама к api
- **геймификация** — XP за каждый день, 5 рангов, прогресс-бар, тепловая карта последних 7 дней
- **парный челлендж** (премиум) — общий стрик с другом через deep link: оба должны отметиться
- **редактор истории** — поправить любой прошедший день задним числом
- **еженедельная сводка** — автоматически в понедельник с AI-комментарием
- **монетизация** — telegram stars: кастомные челленджи (100 ⭐️), заморозки (15/30 ⭐️)

---

## Стек

| Слой | Технологии |
|---|---|
| Bot framework | aiogram 3.x, FSM через RedisStorage |
| Database | SQLite + SQLAlchemy 2.0 async (aiosqlite) |
| AI | Google Gemini 2.5 Flash Lite (`gemini-2.5-flash-lite`), asyncio.Queue rate limiter |
| Payments | Telegram Stars (`currency="XTR"`) |
| Scheduling | APScheduler — чеки, auto-skip, еженедельная статистика |
| Инфраструктура | Docker, Railway.app (SQLite volume mount) |
| Мониторинг | Sentry |

---

## Архитектура

```
main.py          — все хендлеры, FSM, фоновые задачи, middleware
models.py        — User → Challenge → ChallengeDay (cascade delete)
database.py      — async engine + session factory
keyboards.py     — inline/reply keyboard builders
states.py        — FSM states (ChallengeState)
tests/           — unit-тесты чистых функций (pytest)
```

**ключевые решения:**
- `EnsureUserMiddleware` — auto-создаёт User при первом контакте
- idempotency фоновых задач через флаги в БД (`last_notified_at`, `last_weekly_stats_at`)
- timezone без библиотек — UTC offset вычисляется из текущего часа пользователя при онбординге
- AI-очередь через Redis list (`brpop`) — не более 1 запроса к gemini каждые 0.5с, фолбэк на статичные советы при переполнении
- миграции через `ALTER TABLE IF NOT EXISTS` в `init_db()` — без alembic

---

## Запуск локально

```bash
git clone https://github.com/anastasiya-soik/just_do_it_bot.git
cd just_do_it_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполни BOT_TOKEN, ADMIN_ID и остальное — см. таблицу ниже
python main.py
```

### Docker

```bash
docker build -t just_do_it_bot .
docker run --env-file .env -v $(pwd)/data:/data just_do_it_bot
```

### Тесты

```bash
python -m pytest tests/ -v
```

---

## Деплой

Railway.app — автодеплой из `main` ветки. SQLite хранится в volume, примонтированном в `DATA_DIR=/data`.

---

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен от BotFather |
| `ADMIN_ID` | Твой Telegram ID |
| `REDIS_URL` | Опционально, по умолчанию `redis://localhost:6379` |
| `GEMINI_API_KEY` | Опционально — ключ из aistudio.google.com |
| `SENTRY_DSN` | Опционально — трекинг ошибок |
| `DATA_DIR` | Опционально, по умолчанию `./data/` |

---

## Об авторе

Я проджект-менеджер, которая увлеклась разработкой и AI — делаю pet-проекты для себя в формате vibe coding: придумываю идею, итерирую с AI и смотрю, что получается.

just do it — один из таких проектов, другой — [purrse](https://github.com/anastasiya-soik/budget-app), трекер финансов с Telegram Mini App. Если форкаешь — звёздочку в карму :)

---

## Лицензия

MIT
