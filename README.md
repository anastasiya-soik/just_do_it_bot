# 🎯 just do it — Habit Tracker Bot

Telegram bot for breaking bad habits. Track streaks, get AI coaching, earn stars.

[Bot](https://t.me/just_never_do_it_bot)

---

## Features

- Daily check-ins with win/fail buttons
- Streak tracking (7/14/30/60/100 day milestones)
- Freezes (lives) to save streaks when you slip
- AI coach (Google Gemini) responds with encouragement
- Weekly digest with AI commentary
- Gamification: XP, ranks, heatmap of last 7 days
- Telegram Stars for premium (custom challenges, extra freezes)
- History editor (fix past days)

---

## Tech

**Bot:** aiogram 3, FSM via Redis  
**Database:** SQLAlchemy 2 (async), SQLite  
**AI:** Google Gemini 2.5 Flash Lite  
**Scheduling:** APScheduler for checks/stats  
**Deploy:** Railway (SQLite on volume)

---

## Setup

```bash
git clone https://github.com/anastasiya-soik/just_do_it_bot.git
cd just_do_it_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## Docker

```bash
docker build -t just_do_it_bot .
docker run --env-file .env -v $(pwd)/data:/data just_do_it_bot
```

## Test

```bash
python -m pytest tests/ -v
```

---

## Env Vars

```
BOT_TOKEN=<from-botfather>
ADMIN_ID=<your-telegram-id>
REDIS_URL=redis://localhost:6379 (optional)
GEMINI_API_KEY=<from-aistudio.google.com> (optional)
SENTRY_DSN=<optional>
DATA_DIR=./data/ (optional)
```

---

## Deploy

Auto-deploy from `main` branch to Railway. SQLite stored in volume.

---

## About

Testing gamification + AI coaching for habit forming. One of my experiments. Star if useful! ⭐

License: MIT

---
---

# 🎯 just do it — Трекер привычек

Telegram бот для отказа от вредных привычек. Стрики, AI-коуч, Telegram Stars.

[Бот](https://t.me/just_never_do_it_bot)

---

## Что умеет

- Ежедневные чеки с кнопками «победа» / «срыв»
- Отслеживание стриков (вехи 7/14/30/60/100 дней)
- Заморозки (жизни) для спасения стрика при срыве
- AI-коуч (Google Gemini) с поддерживающими ответами
- Еженедельная сводка с AI-комментарием
- Геймификация: XP, ранги, тепловая карта за 7 дней
- Telegram Stars для премиума (свои челленджи, доп заморозки)
- Редактор истории (переправить прошедший день)

---

## Стек

**Бот:** aiogram 3, FSM через Redis  
**БД:** SQLAlchemy 2 (async), SQLite  
**AI:** Google Gemini 2.5 Flash Lite  
**Планировщик:** APScheduler для чеков/статистики  
**Деплой:** Railway (SQLite на volume)

---

## Запуск

```bash
git clone https://github.com/anastasiya-soik/just_do_it_bot.git
cd just_do_it_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## Docker

```bash
docker build -t just_do_it_bot .
docker run --env-file .env -v $(pwd)/data:/data just_do_it_bot
```

## Тесты

```bash
python -m pytest tests/ -v
```

---

## Переменные

```
BOT_TOKEN=<от-botfather>
ADMIN_ID=<твой-telegram-id>
REDIS_URL=redis://localhost:6379 (опционально)
GEMINI_API_KEY=<из-aistudio.google.com> (опционально)
SENTRY_DSN=<опционально>
DATA_DIR=./data/ (опционально)
```

---

## Деплой

Auto-deploy из branch `main` в Railway. SQLite хранится на volume.

---

## О проекте

Тестирую геймификацию + AI-коуч для формирования привычек. Один из экспериментов. Звёздочку если помогает! ⭐

Лицензия: MIT
