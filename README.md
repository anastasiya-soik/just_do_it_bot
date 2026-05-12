# just do it — telegram habit tracker

![Python](https://img.shields.io/badge/Python-3.13-blue)
![aiogram](https://img.shields.io/badge/aiogram-3.x-green)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red)
![Deployed](https://img.shields.io/badge/Deployed-Railway-purple)

telegram-бот для отказа от вредных привычек: сахар, фастфуд, алкоголь, никотин, шортсы — или любой свой челлендж.

👉 **попробовать:** [t.me/just_never_do_it_bot](https://t.me/just_never_do_it_bot)

---

## фичи

- **ежедневный чек** — отдельное уведомление на каждый челлендж в своё время, кнопки «победа ✅» / «срыв 😔»
- **кастомное время уведомлений** — для каждого челленджа своё время напоминания
- **заморозки** — спасают стрик при срыве. копятся за стрики 7/14/30/60/100 дней, можно купить за ⭐️
- **AI-коуч** — gemini генерирует живые ответы при победах, срывах и в итогах недели. пингует тебя сам, присылает краткие сводки о прогрессе
- **геймификация** — XP за каждый день, 5 рангов, прогресс-бар, тепловая карта последних 7 дней
- **парный челлендж** — общий стрик с другом через приглашение: оба должны отметиться каждый день
- **редактор истории** — поправить любой прошедший день задним числом
- **еженедельная сводка** — автоматически в понедельник с AI-комментарием
- **премиум (100 ⭐️)** — кастомные челленджи, парные челленджи с друзьями, покупка заморозок (15/30 ⭐️)

---

## стек

| слой | технологии |
|---|---|
| bot framework | aiogram 3.x, FSM |
| database | PostgreSQL + SQLAlchemy 2.0 async |
| cache / queue | Redis |
| ai | google gemini 2.5 flash lite, asyncio.Queue rate limiter |
| payments | telegram stars (`currency="XTR"`) |
| scheduling | APScheduler — чеки, auto-skip, еженедельная статистика |
| infra | docker, railway.app |
| observability | sentry SDK |

---

## архитектура
main.py          — хендлеры, FSM, фоновые задачи, middleware
models.py        — User → Challenge → ChallengeDay (cascade delete)
database.py      — async engine + session factory
keyboards.py     — inline/reply keyboard builders
states.py        — FSM states (ChallengeState)
tests/           — unit-тесты (pytest)

**ключевые решения:**
- `EnsureUserMiddleware` — auto-создаёт User при первом контакте
- кастомное время уведомлений — APScheduler динамически создаёт job на каждый челлендж
- `_ai_queue` (asyncio.Queue, maxsize=50) — не более 1 запроса к gemini каждые 0.5с, фолбэк на статичные советы при переполнении
- Redis — FSM storage + кэш для rate limiting AI-запросов
- idempotency фоновых задач через флаги в БД

---

## запуск локально

```bash
git clone https://github.com/anastasiya-soik/just_do_it_bot.git
cd just_do_it_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

создай `.env`:
BOT_TOKEN=токен_от_BotFather
ADMIN_ID=твой_telegram_id
GEMINI_API_KEY=ключ_из_aistudio.google.com
DATABASE_URL=postgresql+asyncpg://user:password@localhost/dbname
REDIS_URL=redis://localhost:6379
SENTRY_DSN=https://...   # опционально
DATA_DIR=./data          # опционально

```bash
python main.py
```

### тесты

```bash
python -m pytest tests/ -v
```

---

## деплой

railway.app — автодеплой из main ветки. PostgreSQL и Redis подключены как сервисы Railway.