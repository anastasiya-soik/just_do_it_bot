# 🧊 Just Never Do It — Telegram Detox Tracker

![Python](https://img.shields.io/badge/Python-3.13-blue)
![aiogram](https://img.shields.io/badge/aiogram-3.x-green)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red)
![Docker](https://img.shields.io/badge/Docker-Ready-blue)
![Deployed](https://img.shields.io/badge/Deployed-Railway-purple)

**Just Never Do It** — умный Telegram-бот для отказа от вредных привычек: сахар, фастфуд, алкоголь, никотин, шортсы.

Фокус на **эмпатичный UX**, **геймификацию** и **Telegram Stars монетизацию**.

👉 **Попробовать:** [https://t.me/just_never_do_it_bot](https://t.me/just_never_do_it_bot)

## ✨ Ключевые фичи

- **Единый дневной чек** — одно сообщение со всеми активными челленджами, отмечаешь каждый одним тапом
- **AI-мотивация** — Google Gemini генерирует персональные инсайты: по средам/пятницам, при XP-милстоунах, в итогах недели
- **Еженедельная статистика** — автоматически в понедельник в 10:00 по локальному времени с AI-комментарием
- **Геймификация** — XP за каждый успешный день, система рангов (новичок → легенда), 🧊 заморозки за стрики
- **Заморозки** — спасают стрик при срыве. Копятся за 7/14/30/60/100 дней. Можно купить за ⭐️ Stars
- **Парный челлендж** (премиум) — общий стрик с другом: оба должны отметиться, иначе стрик сбрасывается у обоих
- **Telegram Stars монетизация** — разблокировка безлимитных кастомных челленджей (100 ⭐️), пакеты заморозок (15/30 ⭐️)
- **Редактор истории** — изменить статус любого прошедшего дня с защитой от читерства через JOIN-проверку
- **Аналитика** — тепловая карта за 7 дней, счётчики побед и срывов, прогресс-бар до финиша

## 🛠 Технический стек

- **Backend:** Python 3.13, aiogram 3.x, FSM через MemoryStorage
- **Database:** SQLite, SQLAlchemy 2.0 fully async ORM (aiosqlite)
- **AI:** Google Gemini 2.0 Flash — бесплатный tier, REST через httpx, fallback на статичные советы
- **Payments:** Telegram Stars (`currency="XTR"`) — без карты, без комиссии
- **Scheduling:** APScheduler — ежедневные чеки, midnight auto-skip, еженедельная статистика
- **Infrastructure:** Docker, Railway.app (SQLite volume mount)
- **Observability:** Sentry SDK

## 🏗 Архитектура

- **Единый файл `main.py`** — все хендлеры, FSM, фоновые задачи, middleware
- **`EnsureUserMiddleware`** — auto-создание User при первом контакте, нет `scalar_one()` краша
- **Idempotency** — фоновые задачи защищены флагами в БД (`last_notified_at`, `last_weekly_stats_at`, `last_motivation_at`)
- **Partner challenges** — две Challenge-записи связаны через `partner_challenge_id`, инвайт через Telegram deep link
- **Manual migrations** — новые колонки добавляются через `ALTER TABLE` в `init_db()` (нет Alembic)

## 🚀 Запуск локально

```bash
git clone https://github.com/madamsloika/just_do_it_bot.git
cd just_do_it_bot
pip3 install -r requirements.txt
```

Создай `.env`:
```
BOT_TOKEN=токен_от_BotFather
ADMIN_ID=твой_telegram_id
GEMINI_API_KEY=ключ_из_aistudio.google.com   # опционально
SENTRY_DSN=https://...@....ingest.sentry.io/... # опционально
```

```bash
python3 main.py
```
