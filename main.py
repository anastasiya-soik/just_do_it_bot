import asyncio
import logging
import os
import random
import secrets
import sentry_sdk
import httpx
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, BotCommand, FSInputFile, ErrorEvent, ReplyKeyboardRemove,
    LabeledPrice, PreCheckoutQuery
)
from sqlalchemy import select, and_, update, func
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database import async_session_maker, init_db, engine
from models import User, Challenge, ChallengeDay, ChallengeStatus, DayStatus, PartnerInvite
from states import ChallengeState
from keyboards import (
    main_menu_keyboard, settings_keyboard, freeze_keyboard, set_main_menu, start_date_keyboard,
    onboarding_keyboard,
    BTN_MY_CHALLENGES, BTN_NEW_CHALLENGE, BTN_EDIT_HISTORY, BTN_SETTINGS
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SENTRY_DSN = os.getenv("SENTRY_DSN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
STARS_CUSTOM_PRICE = 100
STARS_FREEZE_1     = 15
STARS_FREEZE_3     = 30
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# --- МОНИТОРИНГ ---
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        send_default_pii=True
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)
router = Router()

class EnsureUserMiddleware(BaseMiddleware):
    """Авто-создаёт юзера при первом контакте, чтобы scalar_one() не падал."""
    async def __call__(self, handler, event, data):
        from aiogram.types import Message, CallbackQuery
        obj = event
        tg_user = None
        if isinstance(obj, Message):
            tg_user = obj.from_user
        elif isinstance(obj, CallbackQuery):
            tg_user = obj.from_user
        if tg_user:
            async with async_session_maker() as session:
                exists = (await session.execute(
                    select(User).where(User.telegram_id == tg_user.id)
                )).scalar_one_or_none()
                if not exists:
                    session.add(User(
                        telegram_id=tg_user.id,
                        username=tg_user.username
                    ))
                    await session.commit()
        return await handler(event, data)

CHALLENGE_NAMES = {
    "no_alcohol":   "🍷 алко-пауза",
    "no_sugar":     "🍰 без сладкого",
    "no_fastfood":  "🍔 пп без рпп",
    "no_nicotine":  "🚬 не курю",
    "no_shortvideo":"📱 без шортсов"
}

TIPS = [
    "дофамин восстанавливается через 14 дней детокса — станет легче радоваться мелочам ✨",
    "первые 3 дня — самые сложные физически, потом в игру вступает психология 🧠",
    "одна ошибка — не поражение, а просто повод проанализировать триггеры 🤝",
    "твоя сила воли — это мышца, она качается каждым твоим «нет» 🦾"
]

# Стрики за которые начисляется заморозка
FREEZE_MILESTONES = {7, 14, 30, 60, 100}

def plural_days(n: int) -> str:
    """Правильное склонение: 1 день, 2 дня, 5 дней"""
    if 11 <= n % 100 <= 19:
        return "дней"
    r = n % 10
    if r == 1:       return "день"
    if r in (2, 3, 4): return "дня"
    return "дней"

# ==========================================
# ЧАСТЬ 1: УТИЛИТЫ И БИЗНЕС-ЛОГИКА
# ==========================================

def get_user_rank(xp: int) -> str:
    if xp < 50:  return "новичок 🌱"
    if xp < 200: return "стоик 🧱"
    if xp < 500: return "мастер контроля 💎"
    return "легенда дисциплины 👑"

def get_progress_bar(percent: int) -> str:
    length = 8
    filled = int(length * max(0, min(100, percent)) / 100)
    return "●" * filled + "○" * (length - filled)

async def get_heatmap(session, challenge_id: int) -> str:
    #Последние 7 дней в виде эмодзи-строки
    res = await session.execute(
        select(ChallengeDay)
        .where(ChallengeDay.challenge_id == challenge_id)
        .order_by(ChallengeDay.date.desc())
        .limit(7)
    )
    history = {d.date: d.status for d in res.scalars().all()}
    line = []
    for i in range(6, -1, -1):
        d = date.today() - timedelta(days=i)
        status = history.get(d)
        if status == DayStatus.success:   line.append("✅")
        elif status == DayStatus.fail:    line.append("😵")
        elif status == DayStatus.skip:    line.append("⏭")
        else:                             line.append("○")
    return "".join(line)

async def recalculate_streak(session, challenge_id: int) -> int:
    c = (await session.execute(
        select(Challenge).where(Challenge.id == challenge_id)
    )).scalar_one()

    res = await session.execute(
        select(ChallengeDay)
        .where(ChallengeDay.challenge_id == challenge_id)
        .order_by(ChallengeDay.date.desc())
    )
    days = {d.date: d.status for d in res.scalars().all()}

    partner_days: dict = {}
    if c.partner_challenge_id:
        res2 = await session.execute(
            select(ChallengeDay).where(ChallengeDay.challenge_id == c.partner_challenge_id)
        )
        partner_days = {d.date: d.status for d in res2.scalars().all()}

    current_streak = 0
    check_date = date.today()
    if check_date not in days:
        check_date -= timedelta(days=1)

    while check_date in days:
        status = days[check_date]
        ok = status in (DayStatus.success, DayStatus.skip)
        if c.partner_challenge_id:
            p_status = partner_days.get(check_date)
            ok = ok and p_status in (DayStatus.success, DayStatus.skip)
        if ok:
            current_streak += 1
        else:
            break
        check_date -= timedelta(days=1)

    c.current_streak = current_streak
    if current_streak > c.longest_streak:
        c.longest_streak = current_streak
    return current_streak

async def check_milestone(event, streak: int, c_name: str, session=None) -> None:
    #Поздравляет при достижении ключевых дней.
    milestones = {
        7:   "неделя! ты в огне 🔥",
        14:  "две недели! так держать 💪",
        30:  "месяц! это уже уровень про 😎",
        60:  "два месяца! ты машина 🤖",
        100: "100 дней! ты легенда 👑"
    }
    if streak not in milestones:
        return

    target = event.message if isinstance(event, CallbackQuery) else event
    gives_freeze = streak in FREEZE_MILESTONES

    text = (
        f"🎉 <b>достижение разблокировано</b>\n"
        f"{c_name}: уже {streak} {plural_days(streak)} — {milestones[streak]}"
    )
    if gives_freeze and session:
        # Определяем пользователя через callback или message
        tg_id = event.from_user.id if isinstance(event, CallbackQuery) else event.from_user.id
        u = (await session.execute(
            select(User).where(User.telegram_id == tg_id)
        )).scalar_one()
        u.freeze_count += 1
        await session.commit()
        text += f"\n\n🧊 получаешь заморозку — теперь их {u.freeze_count}"

    await target.answer(text, parse_mode=ParseMode.HTML)

async def send_with_image(event, image_path: str, caption: str, reply_markup=None):
    #Отправляет фото если файл есть, иначе текстом
    target = event.message if isinstance(event, CallbackQuery) else event
    if os.path.exists(image_path):
        return await target.answer_photo(
            FSInputFile(image_path), caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    return await target.answer(caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

def get_status_kb(c_id: int, d_str: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ выдержал", callback_data=f"save_{c_id}_{d_str}_success"),
            InlineKeyboardButton(text="😵 сорвался",  callback_data=f"save_{c_id}_{d_str}_fail")
        ],
        [InlineKeyboardButton(text="⏭ пропустить", callback_data=f"save_{c_id}_{d_str}_skip")],
        [InlineKeyboardButton(text="❌ закрыть",    callback_data="close_settings")]
    ])

MONTH_NAMES_RU = ["января","февраля","марта","апреля","мая","июня",
                  "июля","августа","сентября","октября","ноября","декабря"]

async def get_ai_motivation(context: str) -> str:
    if not GEMINI_API_KEY:
        return random.choice(TIPS)
    prompt = (
        "Ты — поддерживающий коуч по формированию привычек. "
        "Напиши одно короткое мотивационное сообщение (1-2 предложения) на русском языке. "
        f"Контекст: {context}. "
        "Без лишних слов, без восклицаний через слово, живо и по-человечески."
    )
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                GEMINI_URL,
                params={"key": GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 100, "temperature": 0.9}
                }
            )
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return random.choice(TIPS)

def build_unified_check_kb(
    challenges_with_status: list[tuple],
    d_str: str
) -> InlineKeyboardMarkup:
    rows = []
    status_icons = {DayStatus.success: "✅", DayStatus.fail: "😵", DayStatus.skip: "⏭"}
    for c, day_status in challenges_with_status:
        name = CHALLENGE_NAMES.get(c.challenge_type, c.challenge_type)
        if day_status is not None:
            icon = status_icons.get(day_status, "✅")
            rows.append([InlineKeyboardButton(text=f"{name}  {icon} отмечено", callback_data="noop")])
        else:
            rows.append([
                InlineKeyboardButton(text=name,  callback_data="noop"),
                InlineKeyboardButton(text="✅",   callback_data=f"save_{c.id}_{d_str}_success"),
                InlineKeyboardButton(text="😵",   callback_data=f"save_{c.id}_{d_str}_fail"),
                InlineKeyboardButton(text="⏭",   callback_data=f"save_{c.id}_{d_str}_skip"),
            ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def build_stats_text(session, user) -> tuple[str, InlineKeyboardMarkup]:
    await session.execute(
        update(Challenge)
        .where(and_(
            Challenge.target_date < date.today(),
            Challenge.status == ChallengeStatus.active
        ))
        .values(status=ChallengeStatus.completed, completed_at=date.today())
    )
    await session.commit()

    challenges = (await session.execute(
        select(Challenge).where(and_(
            Challenge.user_id == user.id,
            Challenge.status == ChallengeStatus.active
        ))
    )).scalars().all()

    report = f"{get_user_rank(user.xp)}\n"
    kb_delete = InlineKeyboardMarkup(inline_keyboard=[])

    if not challenges:
        report += (
            "\n\nпока пусто 👀\n\n"
            "запусти первый челлендж — и я начну считать твои победы.\n"
            "жми <b>🎯 новый челлендж</b> в меню"
        )
    else:
        for c in challenges:
            stats_res = await session.execute(
                select(
                    func.count(ChallengeDay.id).filter(ChallengeDay.status == DayStatus.success),
                    func.count(ChallengeDay.id).filter(ChallengeDay.status == DayStatus.fail)
                ).where(ChallengeDay.challenge_id == c.id)
            )
            success_count, fail_count = stats_res.fetchone()
            days_in = max(1, (date.today() - c.start_date).days + 1)
            heatmap = await get_heatmap(session, c.id)
            name = CHALLENGE_NAMES.get(c.challenge_type, c.challenge_type)

            report += f"\n{name}\n"
            report += f"— {c.current_streak} {plural_days(c.current_streak)} подряд\n"
            report += f"— рекорд {c.longest_streak} {plural_days(c.longest_streak)}\n"
            report += f"— {success_count} побед, {fail_count} срывов\n"
            report += f"— эта неделя: {heatmap}\n"

            if c.target_date:
                full_dist = max(1, (c.target_date - c.start_date).days)
                pct = min(100, max(0, int((date.today() - c.start_date).days / full_dist * 100)))
                report += f"— до финиша: {get_progress_bar(pct)} {pct}%\n"

            kb_delete.inline_keyboard.append([
                InlineKeyboardButton(text=f"🗑 отменить {name}", callback_data=f"drop_{c.id}")
            ])

    completed_count = (await session.execute(
        select(func.count(Challenge.id)).where(and_(
            Challenge.user_id == user.id,
            Challenge.status == ChallengeStatus.completed
        ))
    )).scalar()

    report += f"\nзавершено: {completed_count}   заморозок: {user.freeze_count}"
    return report, kb_delete

# ==========================================
# ЧАСТЬ 2: ОНБОРДИНГ И КОМАНДЫ
# ==========================================

@router.errors()
async def error_handler(event: ErrorEvent, bot: Bot):
    logger.exception(f"необработанная ошибка: {event.exception}")
    if SENTRY_DSN:
        sentry_sdk.capture_exception(event.exception)
    try:
        if event.update.message:
            chat_id = event.update.message.chat.id
        else:
            chat_id = event.update.callback_query.message.chat.id
        await bot.send_message(chat_id, "упс, что-то пошло не так — уже чиню 🛠")
    except Exception:
        pass

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("действие отменено", reply_markup=main_menu_keyboard())

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>как пользоваться ботом</b>\n\n"

        "<b>🎯 новый челлендж</b>\n"
        "выбери готовую привычку или создай свою.\n"
        "укажи дату старта и режим:\n"
        "• <b>стрик</b> — бьёшь личный рекорд дней подряд\n"
        "• <b>до даты</b> — идёшь к конкретному финишу\n\n"

        "<b>📊 мой прогресс</b>\n"
        "статистика по активным челленджам: стрик, рекорд, тепловая карта последних 7 дней\n\n"

        "<b>🔔 ежедневный чек</b>\n"
        "каждый вечер бот пришлёт одно сообщение — нажмёшь ✅ или 😵 рядом с каждым челленджем\n\n"

        "<b>📝 поправить день</b>\n"
        "забыл отметить вчера? можно исправить любой прошедший день\n\n"

        "<b>🧊 заморозки</b>\n"
        "копятся автоматически за стрики 7 / 14 / 30 / 60 / 100 дней.\n"
        "использовал заморозку при срыве — день уходит как пропуск, стрик живёт.\n"
        "можно докупить в настройках: 1 за 15 ⭐️ или 3 за 30 ⭐️\n\n"

        "<b>👥 парный челлендж</b> (премиум)\n"
        "позови друга — держите общий стрик вместе.\n"
        "день засчитывается только если оба отметились.\n"
        "доступно после покупки премиума в настройках\n\n"

        "<b>⚙️ настройки</b>\n"
        "время уведомлений, часовой пояс, режим пропущенных дней, покупка заморозок\n\n"

        "📊 статистика за неделю приходит автоматически каждый понедельник в 10:00\n"
        "💡 мотивация — по средам и пятницам в 12:00\n\n"
        "/cancel — отменить любое действие\n"
        "/faq — частые вопросы и советы по мотивации",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❓ частые вопросы", callback_data="faq_back")]
        ])
    )

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    user_count = 0
    async with async_session_maker() as session:
        user_count = (await session.execute(
            select(func.count(User.id)).where(User.utc_offset.is_not(None))
        )).scalar()
    await message.answer(
        f"📢 <b>рассылка</b>\n\n"
        f"активных пользователей: <b>{user_count}</b>\n\n"
        f"напиши текст сообщения (поддерживается HTML-разметка).\n"
        f"/cancel — отмена",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(ChallengeState.broadcast_text)

@router.message(StateFilter(ChallengeState.broadcast_text))
async def do_broadcast(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    text = message.text or message.caption or ""
    if not text:
        return await message.answer("пустое сообщение, отмена")

    async with async_session_maker() as session:
        users = (await session.execute(
            select(User).where(User.utc_offset.is_not(None))
        )).scalars().all()
        tg_ids = [u.telegram_id for u in users]

    sent = 0
    failed = 0
    status_msg = await message.answer(f"⏳ отправляю... 0 / {len(tg_ids)}")
    for i, tg_id in enumerate(tg_ids):
        try:
            await bot.send_message(tg_id, text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
        if (i + 1) % 20 == 0:
            try:
                await status_msg.edit_text(f"⏳ отправляю... {i+1} / {len(tg_ids)}")
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ рассылка завершена\n\n"
        f"отправлено: {sent}\n"
        f"ошибок: {failed}"
    )

@router.message(Command("premium_on"))
async def cmd_premium_on(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.answer("использование: /premium_on &lt;telegram_id или @username&gt;", parse_mode=ParseMode.HTML)
    await _set_premium(message, args[1].strip(), True)

@router.message(Command("premium_off"))
async def cmd_premium_off(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.answer("использование: /premium_off &lt;telegram_id или @username&gt;", parse_mode=ParseMode.HTML)
    await _set_premium(message, args[1].strip(), False)

@router.message(Command("premium_list"))
async def cmd_premium_list(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with async_session_maker() as session:
        users = (await session.execute(
            select(User).where(User.premium_customs == True)
        )).scalars().all()
    if not users:
        return await message.answer("премиум-пользователей нет")
    lines = [f"• {u.telegram_id} (@{u.username or '—'})" for u in users]
    await message.answer(f"⭐️ <b>премиум ({len(users)})</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)

async def _set_premium(message: Message, target: str, value: bool):
    async with async_session_maker() as session:
        if target.startswith("@"):
            username = target.lstrip("@")
            u = (await session.execute(
                select(User).where(User.username == username)
            )).scalar_one_or_none()
        else:
            try:
                tg_id = int(target)
            except ValueError:
                return await message.answer("неверный формат — укажи числовой id или @username")
            u = (await session.execute(
                select(User).where(User.telegram_id == tg_id)
            )).scalar_one_or_none()

        if not u:
            return await message.answer("пользователь не найден")

        u.premium_customs = value
        await session.commit()
        status = "⭐️ выдан" if value else "снят"
        await message.answer(f"{status} премиум для {u.telegram_id} (@{u.username or '—'})")

@router.message(Command("stats_admin"))
async def cmd_stats_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with async_session_maker() as session:
        total_users = (await session.execute(select(func.count(User.id)))).scalar()
        active_users = (await session.execute(
            select(func.count(User.id)).where(User.utc_offset.is_not(None))
        )).scalar()
        total_challenges = (await session.execute(
            select(func.count(Challenge.id)).where(Challenge.status == ChallengeStatus.active)
        )).scalar()
        premium_users = (await session.execute(
            select(func.count(User.id)).where(User.premium_customs == True)
        )).scalar()
    await message.answer(
        f"📊 <b>статистика бота</b>\n\n"
        f"всего пользователей: <b>{total_users}</b>\n"
        f"настроили часовой пояс: <b>{active_users}</b>\n"
        f"активных челленджей: <b>{total_challenges}</b>\n"
        f"premium_customs: <b>{premium_users}</b>",
        parse_mode=ParseMode.HTML
    )

@router.message(CommandStart())
@router.message(F.text.casefold().in_({"старт", "start", "меню", "menu"}))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    # Handle partner invite deep link: /start join_TOKEN
    if message.text and message.text.startswith("/start "):
        param = message.text.split(maxsplit=1)[1]
        if param.startswith("join_"):
            token = param[5:]
            async with async_session_maker() as session:
                invite = (await session.execute(
                    select(PartnerInvite).where(PartnerInvite.token == token)
                )).scalar_one_or_none()
                if not invite:
                    return await message.answer("ссылка недействительна или уже использована 🤷")
                a_challenge = (await session.execute(
                    select(Challenge).where(Challenge.id == invite.challenge_id)
                )).scalar_one()
                a_user = (await session.execute(
                    select(User).where(User.id == a_challenge.user_id)
                )).scalar_one()
            name = CHALLENGE_NAMES.get(a_challenge.challenge_type, a_challenge.challenge_type)
            partner_name = a_user.username or str(a_user.telegram_id)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ принять вызов!", callback_data=f"partner_accept_{token}")],
                [InlineKeyboardButton(text="❌ отклонить",      callback_data="close_settings")]
            ])
            return await message.answer(
                f"👥 <b>приглашение на парный челлендж</b>\n\n"
                f"@{partner_name} зовёт тебя делать <b>{name}</b> вместе.\n"
                "общий стрик — оба должны отмечать каждый день, иначе стрик сбрасывается у обоих.",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )

    async with async_session_maker() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )).scalar_one_or_none()

        if not user or user.utc_offset is None:
            if not user:
                session.add(User(
                    telegram_id=message.from_user.id,
                    username=message.from_user.username
                ))
                await session.commit()

            kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
            ]])
            await send_with_image(
                message, "media/welcome.jpg",
                "привет! я помогаю бросить вредные привычки — через челленджи, стрики и немного геймификации 🧊\n\n"
                "сначала настрою уведомления под твой часовой пояс.\n"
                "напиши <b>который сейчас час</b> — просто цифра, например: <code>14</code>",
                reply_markup=kb_cancel
            )
            await state.set_state(ChallengeState.waiting_for_time)
        else:
            await message.answer("с возвращением 👋 продолжаем?", reply_markup=main_menu_keyboard())

@router.message(StateFilter(ChallengeState.waiting_for_time))
async def set_timezone(message: Message, state: FSMContext):
    #Определяем UTC-offset по текущему часу пользователя.
    if message.text.lower() in ["отмена", "назад"]:
        await state.clear()
        return await message.answer("ок, если передумаешь — жми /start", reply_markup=main_menu_keyboard())
    try:
        user_hour = int(message.text.strip())
        if not (0 <= user_hour <= 23):
            raise ValueError
        now_utc = datetime.now(timezone.utc)
        offset = user_hour - now_utc.hour
        if offset > 12:  offset -= 24
        elif offset < -12: offset += 24

        async with async_session_maker() as session:
            u = (await session.execute(
                select(User).where(User.telegram_id == message.from_user.id)
            )).scalar_one()
            u.utc_offset = offset
            await session.commit()

        onboarding_text = (
            f"✅ UTC{offset:+d} — буду приходить вовремя\n\n"
            "вот как это работает:\n"
            "каждый день отмечаешь победу или срыв — бот пришлёт напоминание.\n"
            "стрики превращаются в XP и звания, за длинные стрики копятся 🧊 заморозки.\n"
            "пропустил день — поправишь задним числом.\n\n"
            "с чего начнём?"
        )
        await message.answer(
            onboarding_text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        await message.answer("👇", reply_markup=onboarding_keyboard())
        await state.clear()

    except ValueError:
        await message.answer(
            "напиши просто час цифрой от 0 до 23\nнапример: <code>14</code>",
            parse_mode=ParseMode.HTML
        )

@router.callback_query(F.data == "onboarding_start")
async def onboarding_start_challenge(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    kb_buttons = [
        [InlineKeyboardButton(text=v, callback_data=f"new_{k}")]
        for k, v in CHALLENGE_NAMES.items()
    ]
    kb_buttons.append([InlineKeyboardButton(text="✍️ свой челлендж", callback_data="new_custom")])
    kb_buttons.append([InlineKeyboardButton(text="❌ отмена",         callback_data="close_settings")])
    await callback.message.answer(
        "какой челлендж запустим?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    )

@router.callback_query(F.data == "onboarding_guide")
async def onboarding_guide_cb(callback: CallbackQuery):
    guide = (
        "▸ <b>выбираешь цель</b>\n"
        "  без алкоголя, сахара, фастфуда, сигарет, шортсов — или свою\n\n"
        "▸ <b>указываешь старт</b>\n"
        "  сегодня или задним числом, если уже начал раньше\n\n"
        "▸ <b>режим</b>\n"
        "  стрик — бьёшь рекорд дней подряд\n"
        "  до даты — идёшь к конкретному дедлайну\n\n"
        "▸ <b>каждый день</b>\n"
        "  бот пришлёт сводку, ты нажмёшь ✅ или 😵\n"
        "  время — любое, настраивается в ⚙️ настройках\n\n"
        "▸ <b>сорвался?</b>\n"
        "  🧊 заморозка сохранит стрик — день уйдёт как пропуск\n"
        "  или поправь вручную через «📝 поправить день»\n\n"
        "▸ <b>заморозки</b>\n"
        "  копятся автоматически: за 7, 14, 30, 60, 100 дней\n"
        "  можно докупить в настройках за ⭐️\n\n"
        "▸ <b>прогресс</b>\n"
        "  XP, звания и тепловая карта — в «📊 мой прогресс»\n"
        "  еженедельная сводка приходит каждый понедельник"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 первый челлендж", callback_data="onboarding_start")],
        [InlineKeyboardButton(text="❌ закрыть",          callback_data="close_settings")],
    ])
    await callback.message.edit_text(guide, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()

@router.callback_query(F.data == "noop")
async def noop_cb(callback: CallbackQuery):
    await callback.answer()

# ==========================================
# ЧАСТЬ 3: МОИ ЧЕЛЛЕНДЖИ И УДАЛЕНИЕ
# ==========================================

@router.message(F.text == BTN_MY_CHALLENGES)
async def my_challenges_cmd(message: Message):
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )).scalar_one()
        report, kb_delete = await build_stats_text(session, u)
        await send_with_image(message, "media/stats.jpg", report, reply_markup=kb_delete)

@router.callback_query(F.data.startswith("drop_"))
async def drop_challenge(callback: CallbackQuery):
    #ИСПРАВЛЕНО (v1 баг): проверяем что челлендж принадлежит именно этому пользователю через JOIN — иначе любой мог удалить чужой челлендж угадав числовой id. 
    c_id = int(callback.data.split("_")[1])
    async with async_session_maker() as session:
        res = await session.execute(
            select(Challenge)
            .join(User)
            .where(and_(
                Challenge.id == c_id,
                User.telegram_id == callback.from_user.id
            ))
        )
        c = res.scalar_one_or_none()
        if c:
            c.status = ChallengeStatus.archived
            await session.commit()
            await callback.answer("челлендж отменён", show_alert=True)
            await callback.message.delete()
        else:
            await callback.answer("ошибка доступа", show_alert=True)

# ==========================================
# ЧАСТЬ 4: СОЗДАНИЕ ЧЕЛЛЕНДЖА
# ==========================================

@router.message(F.text == BTN_NEW_CHALLENGE)
async def new_challenge_start(message: Message, state: FSMContext):
    await state.clear()
    kb_buttons = [
        [InlineKeyboardButton(text=v, callback_data=f"new_{k}")]
        for k, v in CHALLENGE_NAMES.items()
    ]
    kb_buttons.append([InlineKeyboardButton(text="✍️ свой челлендж", callback_data="new_custom")])
    kb_buttons.append([InlineKeyboardButton(text="👥 парный челлендж ⭐️", callback_data="new_partner")])
    kb_buttons.append([InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")])
    await message.answer("какой челлендж запустим?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@router.callback_query(F.data == "new_custom")
async def start_custom_name(callback: CallbackQuery, state: FSMContext):
    async with async_session_maker() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        active_custom = (await session.execute(
            select(func.count(Challenge.id)).where(and_(
                Challenge.user_id == user.id,
                Challenge.status == ChallengeStatus.active,
                Challenge.challenge_type.notin_(CHALLENGE_NAMES.keys())
            ))
        )).scalar()
        if active_custom >= 1 and not user.premium_customs:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐️ разблокировать за {STARS_CUSTOM_PRICE} ⭐️", callback_data="buy_custom_unlimited")],
                [InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")]
            ])
            return await callback.message.edit_text(
                "✍️ <b>свои челленджи — про-функция</b>\n\n"
                "бесплатно доступен 1 кастомный челлендж.\n"
                f"за <b>{STARS_CUSTOM_PRICE} ⭐️</b> — безлимит навсегда 🚀",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
    await callback.message.edit_text("как назовём твой челлендж?\nкоротко, до 30 символов:")
    await state.set_state(ChallengeState.waiting_for_custom_name)

@router.callback_query(F.data == "buy_custom_unlimited")
async def buy_custom_unlimited(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Безлимитные кастомные челленджи",
        description="Создавай любое количество своих челленджей — навсегда, без ограничений",
        payload="custom_unlimited",
        currency="XTR",
        prices=[LabeledPrice(label="Безлимит", amount=STARS_CUSTOM_PRICE)],
        provider_token="",
    )
    await callback.answer()

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@router.callback_query(F.data == "buy_freeze_menu")
async def open_buy_freeze(callback: CallbackQuery):
    await callback.message.edit_text(
        "🧊 <b>заморозки за звёздочки</b>\n\n"
        "заморозка спасает стрик при срыве — день засчитывается как пропуск.\n\n"
        "выбери вариант:",
        reply_markup=freeze_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.callback_query(F.data == "buy_freeze_1")
async def buy_freeze_1(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="1 заморозка",
        description="Защитит стрик от одного срыва",
        payload="freeze_1",
        currency="XTR",
        prices=[LabeledPrice(label="1 заморозка", amount=STARS_FREEZE_1)],
        provider_token="",
    )
    await callback.answer()

@router.callback_query(F.data == "buy_freeze_3")
async def buy_freeze_3(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="3 заморозки",
        description="Запас защиты — три заморозки по выгодной цене",
        payload="freeze_3",
        currency="XTR",
        prices=[LabeledPrice(label="3 заморозки", amount=STARS_FREEZE_3)],
        provider_token="",
    )
    await callback.answer()

@router.message(F.successful_payment)
async def payment_done(message: Message):
    payload = message.successful_payment.invoice_payload
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )).scalar_one()

        if payload == "custom_unlimited":
            u.premium_customs = True
            await session.commit()
            return await message.answer(
                "⭐️ <b>оплачено, спасибо!</b>\n\n"
                "теперь можешь создавать сколько угодно своих челленджей 🚀",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )
        elif payload == "freeze_1":
            u.freeze_count += 1
            await session.commit()
            return await message.answer(
                f"🧊 <b>+1 заморозка!</b> теперь их у тебя: {u.freeze_count}",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )
        elif payload == "freeze_3":
            u.freeze_count += 3
            await session.commit()
            return await message.answer(
                f"🧊🧊🧊 <b>+3 заморозки!</b> теперь их у тебя: {u.freeze_count}",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )

@router.message(StateFilter(ChallengeState.waiting_for_custom_name))
async def process_custom_name(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "назад"]:
        await state.clear()
        return await message.answer("отменено", reply_markup=main_menu_keyboard())
    name = message.text.strip()
    if not name:
        return await message.answer("название не может быть пустым:")
    if len(name) > 30:
        return await message.answer("слишком длинное — напиши до 30 символов:")
    await state.update_data(ctype="custom", custom_name=name)
    kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
    ]])
    await message.answer(
        f"выбран: <b>{name}</b>\nкогда ты начал?",
        reply_markup=start_date_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(ChallengeState.setting_start_date)

@router.callback_query(F.data == "new_partner")
async def new_partner_start(callback: CallbackQuery):
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        if not u.premium_customs:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐️ разблокировать за {STARS_CUSTOM_PRICE} ⭐️", callback_data="buy_custom_unlimited")],
                [InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")]
            ])
            return await callback.message.edit_text(
                "👥 <b>парный челлендж — про-функция</b>\n\n"
                "позови друга и держите общий стрик вместе.\n"
                f"доступно после покупки премиума за <b>{STARS_CUSTOM_PRICE} ⭐️</b>",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
    kb_buttons = [
        [InlineKeyboardButton(text=v, callback_data=f"partner_{k}")]
        for k, v in CHALLENGE_NAMES.items()
    ]
    kb_buttons.append([InlineKeyboardButton(text="✍️ свой челлендж", callback_data="partner_custom")])
    kb_buttons.append([InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")])
    await callback.message.edit_text(
        "👥 <b>парный челлендж</b>\n\nкакой челлендж делаете вместе?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.callback_query(F.data == "partner_custom")
async def partner_custom_name_prompt(callback: CallbackQuery, state: FSMContext):
    kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
    ]])
    await callback.message.edit_text(
        "как назовём ваш совместный челлендж?\nкоротко, до 30 символов:",
        reply_markup=kb_cancel
    )
    await state.set_state(ChallengeState.waiting_for_partner_custom_name)
    await callback.answer()

@router.message(StateFilter(ChallengeState.waiting_for_partner_custom_name))
async def process_partner_custom_name(message: Message, state: FSMContext, bot: Bot):
    if message.text.lower() in ["отмена", "назад"]:
        await state.clear()
        return await message.answer("отменено", reply_markup=main_menu_keyboard())
    name = message.text.strip()
    if not name:
        return await message.answer("название не может быть пустым:")
    if len(name) > 30:
        return await message.answer("слишком длинное — напиши до 30 символов:")

    token = secrets.token_hex(8)
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )).scalar_one()
        c = Challenge(user_id=u.id, challenge_type=name, start_date=date.today())
        session.add(c)
        await session.flush()
        session.add(PartnerInvite(token=token, challenge_id=c.id, created_at=date.today()))
        await session.commit()

    me = await bot.get_me()
    invite_link = f"https://t.me/{me.username}?start=join_{token}"
    await state.clear()
    await message.answer(
        f"👥 <b>парный челлендж создан: {name}</b>\n\n"
        f"отправь другу эту ссылку:\n{invite_link}\n\n"
        "как только он примет — у вас начнётся общий стрик 🔥",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )

@router.callback_query(F.data.startswith("partner_") & ~F.data.startswith("partner_accept_") & ~F.data.startswith("partner_custom"))
async def create_partner_challenge(callback: CallbackQuery, bot: Bot):
    ctype = callback.data.replace("partner_", "")
    if ctype not in CHALLENGE_NAMES:
        return await callback.answer("неизвестный тип", show_alert=True)

    token = secrets.token_hex(8)
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        c = Challenge(user_id=u.id, challenge_type=ctype, start_date=date.today())
        session.add(c)
        await session.flush()
        session.add(PartnerInvite(token=token, challenge_id=c.id, created_at=date.today()))
        await session.commit()

    me = await bot.get_me()
    invite_link = f"https://t.me/{me.username}?start=join_{token}"
    name = CHALLENGE_NAMES[ctype]
    await callback.message.edit_text(
        f"👥 <b>парный челлендж создан: {name}</b>\n\n"
        f"отправь другу эту ссылку:\n{invite_link}\n\n"
        "как только он примет — у вас начнётся общий стрик 🔥",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.callback_query(F.data.startswith("partner_accept_"))
async def accept_partner_challenge(callback: CallbackQuery):
    token = callback.data.replace("partner_accept_", "")
    async with async_session_maker() as session:
        invite = (await session.execute(
            select(PartnerInvite).where(PartnerInvite.token == token)
        )).scalar_one_or_none()
        if not invite:
            return await callback.answer("ссылка недействительна или уже использована", show_alert=True)

        a_challenge = (await session.execute(
            select(Challenge).where(Challenge.id == invite.challenge_id)
        )).scalar_one_or_none()
        if not a_challenge:
            return await callback.answer("челлендж не найден", show_alert=True)

        a_user = (await session.execute(
            select(User).where(User.id == a_challenge.user_id)
        )).scalar_one()
        if a_user.telegram_id == callback.from_user.id:
            return await callback.answer("нельзя принять свой же инвайт 😅", show_alert=True)

        b_user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        b_challenge = Challenge(
            user_id=b_user.id,
            challenge_type=a_challenge.challenge_type,
            start_date=date.today(),
            partner_challenge_id=a_challenge.id
        )
        session.add(b_challenge)
        await session.flush()

        a_challenge.partner_challenge_id = b_challenge.id
        await session.delete(invite)
        await session.commit()

    name = CHALLENGE_NAMES.get(a_challenge.challenge_type, a_challenge.challenge_type)
    partner_name = a_user.username or str(a_user.telegram_id)
    await callback.message.edit_text(
        f"🤝 <b>принято!</b>\n\n"
        f"вы с @{partner_name} теперь делаете <b>{name}</b> вместе.\n"
        "общий стрик начался — держитесь! 💪",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.callback_query(F.data.startswith("new_"))
async def select_start_date_type(callback: CallbackQuery, state: FSMContext):
    #Стандартные челленджи из CHALLENGE_NAMES. new_custom перехватывается выше.
    ctype = callback.data.replace("new_", "")
    # Защита: если сюда всё же попал "custom" — отбиваем
    if ctype not in CHALLENGE_NAMES:
        return await callback.answer("неизвестный тип челленджа", show_alert=True)
    async with async_session_maker() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        dup = await session.execute(
            select(Challenge).where(and_(
                Challenge.user_id == user.id,
                Challenge.challenge_type == ctype,
                Challenge.status == ChallengeStatus.active
            ))
        )
        if dup.scalar_one_or_none():
            return await callback.answer("этот челлендж уже запущен 💪", show_alert=True)
    await state.update_data(ctype=ctype)
    await callback.message.edit_text(
        f"выбран: <b>{CHALLENGE_NAMES[ctype]}</b>\nкогда ты начал?",
        reply_markup=start_date_keyboard(),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "start_today")
async def start_today_flow(callback: CallbackQuery, state: FSMContext):
    await state.update_data(start_date=date.today())
    await ask_for_mode(callback, state)

@router.callback_query(F.data == "start_custom")
async def start_custom_flow(callback: CallbackQuery, state: FSMContext):
    kb_back = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
    ]])
    await callback.message.edit_text(
        "напиши дату старта (ДД.ММ.ГГГГ)\nнапример: <code>01.04.2026</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_back
    )
    await state.set_state(ChallengeState.setting_start_date)

@router.message(StateFilter(ChallengeState.setting_start_date))
async def process_custom_start_date(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "назад"]:
        await state.clear()
        return await message.answer("ок, отменили", reply_markup=main_menu_keyboard())
    try:
        s_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
        if s_date > date.today():
            return await message.answer("дата старта не может быть в будущем")
        await state.update_data(start_date=s_date)
        await ask_for_mode(message, state)
    except ValueError:
        await message.answer(
            "напиши дату как в примере: <code>01.04.2026</code>",
            parse_mode=ParseMode.HTML
        )

async def ask_for_mode(event, state: FSMContext):
    data = await state.get_data()
    display_name = (
        data.get("custom_name")
        if data["ctype"] == "custom"
        else CHALLENGE_NAMES[data["ctype"]]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📈 стрик",   callback_data="m_up"),
            InlineKeyboardButton(text="⏳ до даты", callback_data="m_down")
        ],
        [InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")]
    ])
    text = f"отлично, цель: <b>{display_name}</b>\nвыбери режим:"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    else:
        await event.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(ChallengeState.selecting_mode)

async def _backfill_past_days(session, challenge_id: int, start_date: date) -> None:
    #Заполняет challenge_days за все дни от start_date до вчера включительно статусом success. Используется когда пользователь указал дату старта в прошлом. Пользователь может поправить любой день через редактор истории. Дни за которые запись уже есть — пропускаются (INSERT OR IGNORE логика через проверку перед добавлением, т.к. UniqueConstraint на challenge_id+date).
    yesterday = date.today() - timedelta(days=1)
    if start_date >= date.today():
        return  # Нечего заполнять — старт сегодня или в будущем

    # Получаем уже существующие записи чтобы не дублировать
    existing = await session.execute(
        select(ChallengeDay.date).where(ChallengeDay.challenge_id == challenge_id)
    )
    existing_dates = {row[0] for row in existing.fetchall()}

    current = start_date
    while current <= yesterday:
        if current not in existing_dates:
            session.add(ChallengeDay(
                challenge_id=challenge_id,
                date=current,
                status=DayStatus.success
            ))
        current += timedelta(days=1)

    await session.commit()
    await recalculate_streak(session, challenge_id)
    await session.commit()

@router.callback_query(F.data == "m_up")
async def save_streak_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("ctype"):
        return await callback.answer("сессия устарела — начни создание челленджа заново", show_alert=True)
    start_date    = data["start_date"]
    type_to_save  = data.get("custom_name") if data["ctype"] == "custom" else data["ctype"]
    display_name  = data.get("custom_name") if data["ctype"] == "custom" else CHALLENGE_NAMES[data["ctype"]]
    is_historical = start_date < date.today()

    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        c = Challenge(user_id=u.id, challenge_type=type_to_save, start_date=start_date)
        session.add(c)
        await session.flush()  # Получаем c.id до commit

        if is_historical:
            await _backfill_past_days(session, c.id, start_date)
        else:
            await session.commit()

    days_back = (date.today() - start_date).days
    if is_historical:
        await callback.message.edit_text(
            f"🚀 челлендж <b>{display_name}</b> запущен\n\n"
            f"засчитал {days_back} {plural_days(days_back)} с момента старта как победы — "
            f"если были срывы, поправь через «📝 поправить день»",
            parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(
            f"🚀 челлендж <b>{display_name}</b> запущен",
            parse_mode=ParseMode.HTML
        )
    await state.clear()

@router.callback_query(F.data == "m_down")
async def mode_down_prompt(callback: CallbackQuery, state: FSMContext):
    kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
    ]])
    await callback.message.edit_text(
        "напиши дату финиша (ДД.ММ.ГГГГ)\nнапример: <code>31.12.2026</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_cancel
    )
    await state.set_state(ChallengeState.setting_date)

@router.message(StateFilter(ChallengeState.setting_date))
async def save_deadline_mode(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "назад"]:
        await state.clear()
        return await message.answer("отменено", reply_markup=main_menu_keyboard())
    try:
        t_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
        data   = await state.get_data()
        start_date = data["start_date"]

        if t_date <= date.today():
            return await message.answer(
                "дата финиша должна быть в будущем\nнапример: <code>31.12.2026</code>",
                parse_mode=ParseMode.HTML
            )
        if t_date <= start_date:
            return await message.answer("дата финиша должна быть позже старта")

        type_to_save = data.get("custom_name") if data["ctype"] == "custom" else data["ctype"]
        is_historical = start_date < date.today()

        async with async_session_maker() as session:
            u = (await session.execute(
                select(User).where(User.telegram_id == message.from_user.id)
            )).scalar_one()
            c = Challenge(
                user_id=u.id,
                challenge_type=type_to_save,
                start_date=start_date,
                target_date=t_date
            )
            session.add(c)
            await session.flush()

            if is_historical:
                await _backfill_past_days(session, c.id, start_date)
            else:
                await session.commit()

        days_back = (date.today() - start_date).days
        if is_historical:
            await message.answer(
                f"✅ цель поставлена до <code>{message.text.strip()}</code>\n\n"
                f"засчитал {days_back} {plural_days(days_back)} с момента старта как победы — "
                f"если были срывы, поправь через «📝 поправить день»",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )
        else:
            await message.answer(
                f"✅ цель поставлена до <code>{message.text.strip()}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard()
            )
        await state.clear()
    except ValueError:
        await message.answer(
            "напиши дату нормально: <code>01.01.2027</code>",
            parse_mode=ParseMode.HTML
        )

# ==========================================
# ЧАСТЬ 5: РЕДАКТОР ИСТОРИИ И ЗАМОРОЗКИ
# ==========================================

@router.message(F.text == BTN_EDIT_HISTORY)
async def edit_history_start(message: Message, state: FSMContext):
    async with async_session_maker() as session:
        res = await session.execute(
            select(Challenge)
            .join(User)
            .where(
                User.telegram_id == message.from_user.id,
                Challenge.status == ChallengeStatus.active
            )
        )
        cs = res.scalars().all()
        if not cs:
            return await message.answer("сначала запусти челлендж")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=CHALLENGE_NAMES.get(c.challenge_type, c.challenge_type),
                callback_data=f"ed_{c.id}"
            )]
            for c in cs
        ] + [[InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")]])
        await message.answer("какой челлендж поправим?", reply_markup=kb)
        await state.set_state(ChallengeState.history_selecting_challenge)

@router.callback_query(F.data.startswith("ed_"))
async def ed_date_input(callback: CallbackQuery, state: FSMContext):
    await state.update_data(cid=int(callback.data.split("_")[1]))
    kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
    ]])
    await callback.message.edit_text(
        "за какое число меняем?\nформат: <code>ДД.ММ.ГГГГ</code>",
        reply_markup=kb_cancel,
        parse_mode=ParseMode.HTML
    )
    await state.set_state(ChallengeState.history_entering_date)

@router.message(StateFilter(ChallengeState.history_entering_date))
async def ed_process(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "назад"]:
        await state.clear()
        return await message.answer("ок, отменили", reply_markup=main_menu_keyboard())
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
        if d > date.today():
            return await message.answer("будущее ещё не наступило ⏳")
        data = await state.get_data()
        async with async_session_maker() as session:
            c = (await session.execute(
                select(Challenge).where(Challenge.id == data['cid'])
            )).scalar_one()
            if d < c.start_date:
                return await message.answer(
                    f"челлендж начался только <code>{c.start_date.strftime('%d.%m.%Y')}</code>",
                    parse_mode=ParseMode.HTML
                )
        await message.answer(
            f"что записать за <code>{message.text.strip()}</code>?",
            reply_markup=get_status_kb(data['cid'], message.text.strip()),
            parse_mode=ParseMode.HTML
        )
        await state.clear()
    except ValueError:
        await message.answer(
            "используй формат: <code>01.05.2024</code>",
            parse_mode=ParseMode.HTML
        )

@router.callback_query(F.data.startswith("save_"))
async def save_status(callback: CallbackQuery):
    _, cid, d_str, status = callback.data.split("_")
    d = datetime.strptime(d_str, "%d.%m.%Y").date()
    is_unified = "честный чек на сегодня" in (callback.message.text or "")

    # values captured inside session for use after it closes
    c_type = None
    is_fin = False
    do_unified_all_done = False
    do_unified_update_kb = False
    unified_new_kb = None
    ai_text_for_unified = None
    final_txt = None
    final_img = None

    async with async_session_maker() as session:
        if status == DayStatus.fail:
            u = (await session.execute(
                select(User).join(Challenge).where(Challenge.id == int(cid))
            )).scalar_one()
            if u.freeze_count > 0:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"🧊 спасти стрик (осталось {u.freeze_count})",
                        callback_data=f"frz_{cid}_{d_str}"
                    )],
                    [InlineKeyboardButton(text="😵 признать срыв", callback_data=f"fai_{cid}_{d_str}")]
                ])
                return await callback.message.edit_text(
                    "похоже на срыв... использовать заморозку?",
                    reply_markup=kb
                )

        res_d = await session.execute(
            select(ChallengeDay).where(and_(
                ChallengeDay.challenge_id == int(cid),
                ChallengeDay.date == d
            ))
        )
        day = res_d.scalar_one_or_none()

        if day and day.status == status:
            return await callback.answer("уже записано")

        xp_user = None
        if status == DayStatus.success and (not day or day.status != DayStatus.success):
            xp_user = (await session.execute(
                select(User).join(Challenge).where(Challenge.id == int(cid))
            )).scalar_one()
            xp_user.xp += 10

        if not day:
            session.add(ChallengeDay(challenge_id=int(cid), date=d, status=status))
        else:
            day.status = status

        await session.commit()
        new_streak = await recalculate_streak(session, int(cid))
        await session.commit()

        c = (await session.execute(
            select(Challenge).where(Challenge.id == int(cid))
        )).scalar_one()
        c_type = c.challenge_type
        # Keep partner's streak in sync
        if c.partner_challenge_id:
            await recalculate_streak(session, c.partner_challenge_id)
            await session.commit()

        is_fin = bool(c.target_date and d == c.target_date and status == DayStatus.success)
        if is_fin:
            c.status = ChallengeStatus.completed
            c.completed_at = date.today()
            await session.commit()

        if status == DayStatus.success:
            await check_milestone(callback, new_streak, CHALLENGE_NAMES.get(c_type, ""), session)

        if is_unified and not is_fin:
            if xp_user is None:
                xp_user = (await session.execute(
                    select(User).join(Challenge).where(Challenge.id == int(cid))
                )).scalar_one()

            all_active = (await session.execute(
                select(Challenge).where(and_(
                    Challenge.user_id == xp_user.id,
                    Challenge.status == ChallengeStatus.active
                ))
            )).scalars().all()

            challenges_with_status = []
            all_done = True
            for ch in all_active:
                day_rec = (await session.execute(
                    select(ChallengeDay).where(and_(
                        ChallengeDay.challenge_id == ch.id,
                        ChallengeDay.date == d
                    ))
                )).scalar_one_or_none()
                if day_rec is None:
                    all_done = False
                challenges_with_status.append((ch, day_rec.status if day_rec else None))

            if all_done:
                do_unified_all_done = True
                ai_text_for_unified = await get_ai_motivation(
                    f"выполнил все челленджи за день, XP: {xp_user.xp}, стрик: {new_streak} дней"
                )
            else:
                do_unified_update_kb = True
                unified_new_kb = build_unified_check_kb(challenges_with_status, d_str)

    if do_unified_all_done:
        await callback.message.edit_text(
            f"✅ всё отмечено на сегодня!\n\n💡 {ai_text_for_unified}",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()
        return

    if do_unified_update_kb:
        await callback.message.edit_reply_markup(reply_markup=unified_new_kb)
        await callback.answer()
        return

    if is_fin:
        final_txt = f"🏆 <b>ПОЗДРАВЛЯЮ!</b> ты дошёл до цели!\n{CHALLENGE_NAMES.get(c_type, '')} завершён"
        final_img = "media/success.jpg"
    elif status == DayStatus.success:
        final_txt = random.choice([
            "ещё один день в копилку 💪 так держать!",
            "сделано. ты молодец, серьёзно 🚀",
            "день засчитан. стрик растёт ✅",
        ])
        final_img = "media/success.jpg"
    else:
        final_txt = random.choice([
            "ничего, завтра новый шанс 🤝",
            "срыв — это не конец, это данные для анализа 🧠",
            "все падают. важно — встать. завтра снова 💪",
        ])
        final_img = "media/reset.jpg"

    await callback.message.delete()
    await send_with_image(callback, final_img, final_txt, reply_markup=main_menu_keyboard())

@router.callback_query(F.data.startswith("frz_"))
async def use_freeze(callback: CallbackQuery):
    #Тратит заморозку: записывает skip вместо fail, стрик сохраняется
    _, cid, d_str = callback.data.split("_")
    d = datetime.strptime(d_str, "%d.%m.%Y").date()
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).join(Challenge).where(Challenge.id == int(cid))
        )).scalar_one()
        u.freeze_count -= 1

        res_d = await session.execute(
            select(ChallengeDay).where(and_(
                ChallengeDay.challenge_id == int(cid),
                ChallengeDay.date == d
            ))
        )
        day = res_d.scalar_one_or_none()
        if not day:
            session.add(ChallengeDay(challenge_id=int(cid), date=d, status=DayStatus.skip))
        else:
            day.status = DayStatus.skip

        await session.commit()
        await recalculate_streak(session, int(cid))
        await session.commit()

    await callback.message.edit_text("🧊 заморозка активирована — стрик в безопасности")
    await callback.answer()

@router.callback_query(F.data.startswith("fai_"))
async def confirm_fail(callback: CallbackQuery):
    #Подтверждение срыва после отказа от заморозки
    _, cid, d_str = callback.data.split("_")
    d = datetime.strptime(d_str, "%d.%m.%Y").date()
    async with async_session_maker() as session:
        res_d = await session.execute(
            select(ChallengeDay).where(and_(
                ChallengeDay.challenge_id == int(cid),
                ChallengeDay.date == d
            ))
        )
        day = res_d.scalar_one_or_none()
        if not day:
            session.add(ChallengeDay(challenge_id=int(cid), date=d, status=DayStatus.fail))
        else:
            day.status = DayStatus.fail
        await session.commit()
        await recalculate_streak(session, int(cid))
        await session.commit()

    await callback.message.delete()
    await send_with_image(
        callback, "media/reset.jpg",
        "всё ровно, завтра — новый старт 🤝",
        reply_markup=main_menu_keyboard()
    )

# ==========================================
# ЧАСТЬ 6: НАСТРОЙКИ
# ==========================================

@router.message(F.text == BTN_SETTINGS)
async def open_settings(message: Message):
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )).scalar_one()
        await message.answer(
            "подкрутим настройки под тебя? ⚙️",
            reply_markup=settings_keyboard(u.silent_mode, u.missed_day_policy, u.report_time)
        )

@router.callback_query(F.data == "set_tz_prompt")
async def tz_prompt_call(callback: CallbackQuery, state: FSMContext):
    #Смена часового пояса из настроек — тот же флоу что и в онбординге
    kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
    ]])
    await callback.message.answer(
        "напиши который сейчас час чтобы обновить пояс\n"
        "просто цифра, например: <code>14</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_cancel
    )
    await state.set_state(ChallengeState.waiting_for_time)
    await callback.answer()

@router.callback_query(F.data == "set_time_prompt")
async def set_time_call(callback: CallbackQuery, state: FSMContext):
    kb_cancel = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")
    ]])
    await callback.message.answer(
        "напиши новое время для ежедневных отчётов (ЧЧ:ММ)\nнапример: <code>21:00</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_cancel
    )
    await state.set_state(ChallengeState.setting_report_time)
    await callback.answer()

@router.message(StateFilter(ChallengeState.setting_report_time))
async def save_report_time(message: Message, state: FSMContext):
    if message.text.lower() in ["отмена", "назад"]:
        await state.clear()
        return await message.answer("ок", reply_markup=main_menu_keyboard())
    try:
        new_t = datetime.strptime(message.text.strip(), "%H:%M").strftime("%H:%M")
        async with async_session_maker() as session:
            u = (await session.execute(
                select(User).where(User.telegram_id == message.from_user.id)
            )).scalar_one()
            u.report_time = new_t
            await session.commit()
        await message.answer(
            f"✅ время обновлено: <code>{new_t}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer(
            "формат: <code>ЧЧ:ММ</code> (например, <code>22:00</code>)",
            parse_mode=ParseMode.HTML
        )

@router.callback_query(F.data.in_({"toggle_silent", "toggle_policy"}))
async def toggles(callback: CallbackQuery):
    async with async_session_maker() as session:
        u = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        if callback.data == "toggle_silent":
            u.silent_mode = not u.silent_mode
        else:
            u.missed_day_policy = (
                DayStatus.fail if u.missed_day_policy == DayStatus.skip else DayStatus.skip
            )
        await session.commit()
        await callback.message.edit_reply_markup(
            reply_markup=settings_keyboard(u.silent_mode, u.missed_day_policy, u.report_time)
        )

@router.callback_query(F.data == "close_settings")
async def close_kb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()

# ==========================================
# ЧАСТЬ 7: ПЛАНИРОВЩИК
# ==========================================

async def daily_task(bot: Bot):
    async with async_session_maker() as session:
        now = datetime.now(timezone.utc)

        res = await session.execute(
            select(User).where(User.utc_offset.is_not(None))
        )
        for u in res.scalars():
            local_t    = now + timedelta(hours=u.utc_offset)
            user_today = local_t.date()

            if u.last_notified_at == user_today:
                continue

            rh, rm = map(int, u.report_time.split(":"))
            if local_t.hour != rh or local_t.minute != rm:
                continue

            cs = (await session.execute(
                select(Challenge).where(and_(
                    Challenge.user_id == u.id,
                    Challenge.status == ChallengeStatus.active
                ))
            )).scalars().all()

            if not cs:
                continue

            # AI-инсайт каждые 30 XP (вместо статичных TIPS)
            if u.xp > 0 and u.xp % 30 == 0:
                try:
                    tip = await get_ai_motivation(f"пользователь набрал {u.xp} XP в трекере привычек")
                    await bot.send_message(
                        u.telegram_id,
                        f"💡 <b>инсайт дня:</b>\n{tip}",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass

            # Собираем только незаполненные челленджи
            d_str = user_today.strftime("%d.%m.%Y")
            unchecked = []
            for c in cs:
                day_rec = (await session.execute(
                    select(ChallengeDay).where(and_(
                        ChallengeDay.challenge_id == c.id,
                        ChallengeDay.date == user_today
                    ))
                )).scalar_one_or_none()
                if not day_rec:
                    unchecked.append(c)

            if unchecked:
                date_label = f"{user_today.day} {MONTH_NAMES_RU[user_today.month - 1]}"
                challenges_with_status = [(c, None) for c in unchecked]
                kb = build_unified_check_kb(challenges_with_status, d_str)
                try:
                    await bot.send_message(
                        u.telegram_id,
                        f"🔔 честный чек на сегодня, {date_label}",
                        reply_markup=kb,
                        disable_notification=u.silent_mode,
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass

            u.last_notified_at = user_today
            await session.commit()

async def auto_skip_task():
    #Запускается каждую минуту. Для пользователей у кого сейчас местная полночь — закрывает вчерашний день по политике (skip или fail). ИСПРАВЛЕНО (v2 баг): в v2 была попытка вычислить (now_utc.hour + User.utc_offset) % 24 прямо в SQL WHERE — это сравнение Python-int с SQLAlchemy-Column, что даёт TypeError. Теперь фильтр по utc_offset != None, а проверка local_hour == 0 делается в Python.
    async with async_session_maker() as session:
        now_utc   = datetime.now(timezone.utc)
        yesterday = date.today() - timedelta(days=1)

        res = await session.execute(
            select(User).where(User.utc_offset.is_not(None))
        )
        for u in res.scalars():
            local_hour = (now_utc.hour + u.utc_offset) % 24
            if local_hour != 0:
                continue

            cs = (await session.execute(
                select(Challenge).where(and_(
                    Challenge.user_id == u.id,
                    Challenge.status == ChallengeStatus.active
                ))
            )).scalars().all()

            for c in cs:
                day_res = await session.execute(
                    select(ChallengeDay).where(and_(
                        ChallengeDay.challenge_id == c.id,
                        ChallengeDay.date == yesterday
                    ))
                )
                if not day_res.scalar_one_or_none():
                    session.add(ChallengeDay(
                        challenge_id=c.id,
                        date=yesterday,
                        status=u.missed_day_policy
                    ))
                    await session.commit()
                    await recalculate_streak(session, c.id)
                    await session.commit()

async def motivation_task(bot: Bot):
    # Среда (2) и пятница (4) в 12:00 по локальному времени — короткое AI-сообщение
    MOTIVATION_DAYS = {2, 4}
    async with async_session_maker() as session:
        now_utc = datetime.now(timezone.utc)
        res = await session.execute(select(User).where(User.utc_offset.is_not(None)))
        for u in res.scalars():
            local_t = now_utc + timedelta(hours=u.utc_offset)
            if local_t.weekday() not in MOTIVATION_DAYS or local_t.hour != 12:
                continue
            user_today = local_t.date()
            if u.last_motivation_at == user_today:
                continue

            # Контекст для AI: текущий стрик по активным челленджам
            cs = (await session.execute(
                select(Challenge).where(and_(
                    Challenge.user_id == u.id,
                    Challenge.status == ChallengeStatus.active
                ))
            )).scalars().all()
            if not cs:
                continue

            max_streak = max((c.current_streak for c in cs), default=0)
            day_name = "среда" if local_t.weekday() == 2 else "пятница"
            context = (
                f"{day_name}, середина недели в трекере привычек. "
                f"лучший текущий стрик пользователя: {max_streak} дней подряд"
            )
            tip = await get_ai_motivation(context)

            try:
                await bot.send_message(
                    u.telegram_id,
                    f"💡 {tip}",
                    parse_mode=ParseMode.HTML,
                    disable_notification=u.silent_mode
                )
                u.last_motivation_at = user_today
                await session.commit()
            except Exception:
                pass

async def weekly_stats_task(bot: Bot):
    async with async_session_maker() as session:
        now_utc = datetime.now(timezone.utc)
        res = await session.execute(select(User).where(User.utc_offset.is_not(None)))
        for u in res.scalars():
            local_t = now_utc + timedelta(hours=u.utc_offset)
            # Понедельник в 10:00 по локальному времени
            if local_t.weekday() != 0 or local_t.hour != 10:
                continue
            user_today = local_t.date()
            if u.last_weekly_stats_at == user_today:
                continue

            report, _ = await build_stats_text(session, u)

            # Считаем % побед за прошлую неделю для AI-комментария
            week_start = user_today - timedelta(days=7)
            week_stats = (await session.execute(
                select(
                    func.count(ChallengeDay.id).filter(ChallengeDay.status == DayStatus.success),
                    func.count(ChallengeDay.id)
                ).join(Challenge).where(and_(
                    Challenge.user_id == u.id,
                    ChallengeDay.date >= week_start,
                    ChallengeDay.date < user_today
                ))
            )).fetchone()
            week_success, week_total = week_stats
            week_pct = int(week_success / max(1, week_total) * 100)

            ai_text = await get_ai_motivation(
                f"итоги недели: {week_pct}% побед из {week_total} возможных дней в трекере привычек"
            )
            full_report = f"📊 <b>итоги недели</b>\n\n{report}\n\n💡 {ai_text}"

            try:
                await bot.send_message(
                    u.telegram_id,
                    full_report,
                    parse_mode=ParseMode.HTML,
                    disable_notification=u.silent_mode
                )
                u.last_weekly_stats_at = user_today
                await session.commit()
            except Exception:
                pass

@router.message(Command("faq"))
async def cmd_faq(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="😔 нет мотивации продолжать",    callback_data="faq_motivation")],
        [InlineKeyboardButton(text="🧊 как работают заморозки?",     callback_data="faq_freeze")],
        [InlineKeyboardButton(text="📝 можно поправить прошлый день?", callback_data="faq_edit")],
        [InlineKeyboardButton(text="👥 что такое парный челлендж?",   callback_data="faq_partner")],
        [InlineKeyboardButton(text="⭐️ за что платить звёздочки?",   callback_data="faq_stars")],
        [InlineKeyboardButton(text="❌ закрыть",                      callback_data="close_settings")],
    ])
    await message.answer("❓ <b>частые вопросы</b>\n\nвыбери что интересует:", reply_markup=kb, parse_mode=ParseMode.HTML)

@router.callback_query(F.data.startswith("faq_"))
async def faq_answer(callback: CallbackQuery):
    topic = callback.data.replace("faq_", "")
    kb_back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← назад к вопросам", callback_data="faq_back")],
    ])
    answers = {
        "motivation": (
            "😔 <b>нет мотивации — это нормально</b>\n\n"
            "мотивация приходит и уходит, а привычка — это система.\n\n"
            "попробуй:\n"
            "• сократить челлендж до минимума — просто отметь сегодняшний день\n"
            "• вспомни зачем начал — запиши это прямо сейчас\n"
            "• посмотри свой стрик в «📊 мой прогресс» — жалко терять?\n\n"
            "если совсем тяжело — используй 🧊 заморозку, стрик сохранится"
        ),
        "freeze": (
            "🧊 <b>заморозки</b>\n\n"
            "когда нажимаешь 😵 срыв — бот предложит потратить заморозку.\n"
            "день засчитается как «пропуск» и стрик не сбросится.\n\n"
            "заморозки копятся автоматически за стрики:\n"
            "7 / 14 / 30 / 60 / 100 дней\n\n"
            "закончились — купи в ⚙️ настройках:\n"
            "1 шт за 15 ⭐️ или 3 шт за 30 ⭐️"
        ),
        "edit": (
            "📝 <b>поправить прошлый день</b>\n\n"
            "да, можно — нажми «📝 поправить день» в меню.\n"
            "выбери челлендж → введи дату (ДД.ММ.ГГГГ) → выбери новый статус.\n\n"
            "стрик пересчитается автоматически."
        ),
        "partner": (
            "👥 <b>парный челлендж</b>\n\n"
            "общий стрик с другом — держитесь вместе.\n\n"
            "как работает:\n"
            "• создаёшь парный челлендж в «🎯 новый челлендж»\n"
            "• получаешь ссылку-инвайт и отправляешь другу\n"
            "• друг принимает — стрик запускается для обоих\n\n"
            "важно: день засчитывается только если <b>оба</b> отметились.\n"
            "если один из вас не отметил — стрик сбрасывается у обоих.\n\n"
            "доступно с премиумом (100 ⭐️)"
        ),
        "stars": (
            "⭐️ <b>за что платить звёздочки</b>\n\n"
            "<b>100 ⭐️ — премиум навсегда:</b>\n"
            "• безлимитные кастомные челленджи\n"
            "• парные челленджи с друзьями\n\n"
            "<b>15 ⭐️ — 1 заморозка</b>\n"
            "<b>30 ⭐️ — 3 заморозки</b>\n\n"
            "купить можно в ⚙️ настройках"
        ),
    }
    if topic == "back":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="😔 нет мотивации продолжать",    callback_data="faq_motivation")],
            [InlineKeyboardButton(text="🧊 как работают заморозки?",     callback_data="faq_freeze")],
            [InlineKeyboardButton(text="📝 можно поправить прошлый день?", callback_data="faq_edit")],
            [InlineKeyboardButton(text="👥 что такое парный челлендж?",   callback_data="faq_partner")],
            [InlineKeyboardButton(text="⭐️ за что платить звёздочки?",   callback_data="faq_stars")],
            [InlineKeyboardButton(text="❌ закрыть",                      callback_data="close_settings")],
        ])
        await callback.message.edit_text("❓ <b>частые вопросы</b>\n\nвыбери что интересует:", reply_markup=kb, parse_mode=ParseMode.HTML)
        return await callback.answer()

    text = answers.get(topic)
    if not text:
        return await callback.answer()
    await callback.message.edit_text(text, reply_markup=kb_back, parse_mode=ParseMode.HTML)
    await callback.answer()

# rate limit: 5 сообщений в минуту на пользователя
from collections import defaultdict
_fallback_timestamps: dict[int, list] = defaultdict(list)

FALLBACK_RATE_LIMIT = 5      # сообщений
FALLBACK_RATE_WINDOW = 60    # секунд

FALLBACK_LIMIT_REPLIES = [
    "окей окей, полегче 😅 подожди минутку и пиши снова",
    "ты меня заспамишь так 😵 минуту передышки — и продолжим",
    "стоп, я не успеваю думать 🤯 минута тишины и я твой",
]

@router.message()
async def fallback_echo(message: Message):
    if not message.text:
        return await message.answer("нажми на кнопку или введи /cancel", reply_markup=main_menu_keyboard())

    # Rate limiting
    uid = message.from_user.id
    now = datetime.now(timezone.utc).timestamp()
    _fallback_timestamps[uid] = [t for t in _fallback_timestamps[uid] if now - t < FALLBACK_RATE_WINDOW]
    if len(_fallback_timestamps[uid]) >= FALLBACK_RATE_LIMIT:
        return await message.answer(random.choice(FALLBACK_LIMIT_REPLIES))
    _fallback_timestamps[uid].append(now)

    user_text = message.text
    prompt = (
        "ты — дерзкий, остроумный ассистент Telegram-бота 'Just Never Do It' для трекинга вредных привычек. "
        "всегда пишешь с маленькой буквы, коротко, живо, иногда с лёгким юмором. "
        "подстраиваешься под стиль пользователя: если он пишет неформально — ты тоже, если серьёзно — чуть серьёзнее. "
        "кнопки в боте (используй только эти названия): "
        "«📊 мой прогресс», «🎯 новый челлендж», «📝 поправить день», «⚙️ настройки». "
        "команды: /help — гайд, /faq — частые вопросы, /cancel — отмена. "
        "бот умеет: создавать челленджи (алкоголь, сахар, фастфуд, никотин, шортсы или свой), "
        "отмечать дни (✅ победа / 😵 срыв / ⏭ пропуск), считать стрики, "
        "редактировать историю, использовать 🧊 заморозки для сохранения стрика. "
        f"пользователь написал: «{user_text}». "
        "ответь 1-2 предложения по-русски. "
        "если жалуется на мотивацию или лень — поддержи с юмором и намекни на /faq. "
        "если спрашивает как пользоваться — /help. "
        "не придумывай несуществующие кнопки и команды."
    )
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                GEMINI_URL,
                params={"key": GEMINI_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 100, "temperature": 0.95}
                }
            )
            reply = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        reply = "что-то пошло не так у меня в голове 🤯 попробуй /faq или нажми на кнопку в меню"

    await message.answer(reply, reply_markup=main_menu_keyboard())

# ==========================================
# ЧАСТЬ 8: ЗАПУСК
# ==========================================

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(EnsureUserMiddleware())
    dp.callback_query.middleware(EnsureUserMiddleware())
    dp.include_router(router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_task,        "interval", minutes=1,  args=[bot])
    scheduler.add_job(auto_skip_task,    "interval", minutes=1)
    scheduler.add_job(weekly_stats_task, "interval", minutes=60, args=[bot])
    scheduler.add_job(motivation_task,   "interval", minutes=60, args=[bot])
    scheduler.start()

    await set_main_menu(bot)
    logger.info("бот запущен, все системы активны 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)
        logger.exception(f"критическая ошибка при запуске: {e}")