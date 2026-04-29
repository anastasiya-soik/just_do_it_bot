# just do it — telegram habit tracker

![Python](https://img.shields.io/badge/Python-3.13-blue)
![aiogram](https://img.shields.io/badge/aiogram-3.x-green)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red)
![Deployed](https://img.shields.io/badge/Deployed-Railway-purple)

telegram-бот для отказа от вредных привычек: сахар, фастфуд, алкоголь, никотин, шортсы — или любой свой челлендж.

👉 **попробовать:** [t.me/just_never_do_it_bot](https://t.me/just_never_do_it_bot)

---

## фичи

- **ежедневный чек** — отдельное сообщение на каждый челлендж с кнопками «победа ✅» / «срыв 😔»
- **заморозки** — спасают стрик при срыве. копятся автоматически за стрики 7/14/30/60/100 дней, можно купить за ⭐️
- **AI-коуч** — google gemini генерирует короткие живые ответы: при победах, срывах, в итогах недели. очередь запросов защищает от спама к api
- **геймификация** — XP за каждый день, 5 рангов, прогресс-бар, тепловая карта последних 7 дней
- **парный челлендж** (премиум) — общий стрик с другом через deep link: оба должны отметиться
- **редактор истории** — поправить любой прошедший день задним числом
- **еженедельная сводка** — автоматически в понедельник с AI-комментарием
- **монетизация** — telegram stars: кастомные челленджи (100 ⭐️), заморозки (15/30 ⭐️)

---

## стек

| слой | технологии |
|---|---|
| bot framework | aiogram 3.x, FSM через MemoryStorage |
| database | SQLite + SQLAlchemy 2.0 async (aiosqlite) |
| ai | google gemini 2.5 flash lite, asyncio.Queue rate limiter |
| payments | telegram stars (`currency="XTR"`) |
| scheduling | APScheduler — чеки, auto-skip, еженедельная статистика |
| infra | docker, railway.app (sqlite volume mount) |
| observability | sentry SDK |

---

## архитектура

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
- `_ai_queue` (asyncio.Queue, maxsize=50) — не более 1 запроса к gemini каждые 0.5с, фолбэк на статичные советы при переполнении
- миграции через `ALTER TABLE IF NOT EXISTS` в `init_db()` — без alembic

---

## запуск локально

```bash
git clone https://github.com/madamsloika/just_do_it_bot.git
cd just_do_it_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

создай `.env`:
```
BOT_TOKEN=токен_от_BotFather
ADMIN_ID=твой_telegram_id
GEMINI_API_KEY=ключ_из_aistudio.google.com   # опционально
SENTRY_DSN=https://...                        # опционально
DATA_DIR=./data                               # опционально, по умолчанию ./data/
```

```bash
python main.py
```

### тесты

```bash
python -m pytest tests/ -v
```

---

## деплой

railway.app — автодеплой из main ветки. sqlite хранится в volume примонтированном в `DATA_DIR=/data`.
