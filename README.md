# 🧊 Just Never Do It — Telegram Detox Tracker

![Python](https://img.shields.io/badge/Python-3.13-blue)
![aiogram](https://img.shields.io/badge/aiogram-3.x-green)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red)
![Docker](https://img.shields.io/badge/Docker-Ready-blue)
![Deployed](https://img.shields.io/badge/Deployed-Railway-purple)

**Just Never Do It** — это умный Telegram-бот, созданный для комфортного отказа от вредных привычек (сахар, фастфуд, курение, думскроллинг). 

Проект разработан с фокусом на **эмпатичный UX**, **геймификацию** и **отказоустойчивую архитектуру**.

👉 **Попробовать бота:**[(https://t.me/just_never_do_it_bot)]

## ✨ Ключевые фичи (Product Features)
- **Умные часовые пояса:** Бот автоматически высчитывает UTC-смещение пользователя на основе локального времени. Уведомления приходят вовремя в любой точке мира.
- **Геймификация и Уровни:** Начисление XP за успешные дни, система рангов (от "Новичка" до "Легенды"), выдача "Заморозок" за длинные стрики.
- **Инвентарь "Заморозок" (Streak Freeze):** Возможность спасти свой стрик при разовом срыве (вдохновлено механикой Duolingo).
- **Машина времени (Редактор истории):** Возможность изменить статус любого прошедшего дня с защитой от читерства (нельзя править будущее или дни до старта).
- **Продвинутая аналитика:** Генерация тепловой карты за последние 7 дней (✅😵⏭○), расчет % эффективности, счетчики побед и срывов.
- **Асинхронные фоновые задачи:** Интегрирован `APScheduler` для ежедневных чеков и автоматического закрытия пропущенных дней.

## 🛠 Технический стек (Tech Stack)
- **Backend:** Python 3.13, aiogram 3.x
- **Database:** SQLite / PostgreSQL, SQLAlchemy 2.0 (fully async ORM)
- **Task Scheduling:** APScheduler (Background background jobs)
- **Infrastructure & DevOps:** Docker, Dockerfile, Railway.app
- **Observability:** Sentry SDK (Error tracking & Performance monitoring)

## 🏗 Архитектура и Безопасность (SRE & Security)
- **Throttling Middleware:** Защита базы данных от спама кнопками (Rate Limiting).
- **State Management:** Использование FSM (Finite State Machine) для сложных многошаговых диалогов.
- **Idempotency:** Фоновые задачи защищены от двойного выполнения через проверку флагов в БД (`last_notified_at`).
- **Data Integrity:** Использование `UniqueConstraint` и каскадного удаления (`cascade="all, delete-orphan"`) в SQLAlchemy.

## 🚀 Запуск проекта локально

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/ВАШ_НИК/just_do_it_bot.git
2. Создайте виртуальное окружение и установите зависимости:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
3. Создайте файл .env в корне проекта:
   ```bash
   BOT_TOKEN=ваш_токен_от_BotFather
   SENTRY_DSN=ваш_ключ_sentry_опционально
4. Запустите бота:
   ```bash
   python main.py
