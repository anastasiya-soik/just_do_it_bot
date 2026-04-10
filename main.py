import asyncio
import logging
import os
import random
import sentry_sdk
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, BotCommand, FSInputFile, ErrorEvent, ReplyKeyboardRemove
)
from sqlalchemy import select, and_, update, func
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database import async_session_maker, init_db, engine
from models import User, Challenge, ChallengeDay, ChallengeStatus, DayStatus
from states import ChallengeState
from keyboards import (
    main_menu_keyboard, settings_keyboard, set_main_menu, start_date_keyboard,
    BTN_MY_CHALLENGES, BTN_NEW_CHALLENGE, BTN_EDIT_HISTORY, BTN_SETTINGS
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SENTRY_DSN = os.getenv("SENTRY_DSN")

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
   #Пересчёт стрика по истории дней.
    res = await session.execute(
        select(ChallengeDay)
        .where(ChallengeDay.challenge_id == challenge_id)
        .order_by(ChallengeDay.date.desc())
    )
    days = {d.date: d.status for d in res.scalars().all()}

    current_streak = 0
    check_date = date.today()
    # Если сегодня ещё нет отметки — начинаем со вчера
    if check_date not in days:
        check_date -= timedelta(days=1)

    while check_date in days:
        status = days[check_date]
        if status in (DayStatus.success, DayStatus.skip):
            current_streak += 1
        else:
            break
        check_date -= timedelta(days=1)

    c = (await session.execute(
        select(Challenge).where(Challenge.id == challenge_id)
    )).scalar_one()
    c.current_streak = current_streak
    if current_streak > c.longest_streak:
        c.longest_streak = current_streak
    # Коммит делает вызывающая сторона
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
        "отмечай успехи каждый вечер.\n"
        "если забыл — кнопка <b>«📝 поправить день»</b> в помощь 🤝",
        parse_mode=ParseMode.HTML
    )

@router.message(CommandStart())
@router.message(F.text.casefold().in_({"старт", "start", "меню", "menu"}))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
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
                "хей! я помогу строить контроль над привычками ✨\n\n"
                "чтобы присылать напоминания вовремя, скажи который сейчас час?\n"
                "просто цифра, например: <code>14</code>",
                reply_markup=kb_cancel
            )
            await state.set_state(ChallengeState.waiting_for_time)
        else:
            await message.answer("о, приветы! продолжаем?", reply_markup=main_menu_keyboard())

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

        guide = (
            f"✅ понял, UTC{offset:+d} — буду приходить вовремя\n\n"
            "▸ цель\n"
            "  без алкоголя, сахара, фастфуда, сигарет или шортсов\n\n"
            "▸ старт\n"
            "  сегодня или задним числом — если уже начал раньше\n\n"
            "▸ режим\n"
            "  стрик — бьёшь личный рекорд дней подряд\n"
            "  до даты — идёшь к конкретному финишу\n\n"
            "▸ напоминания\n"
            "  в любое время — ты сам выставляешь когда удобно\n\n"
            "▸ если сорвался\n"
            "  заморозка сохранит стрик\n"
            "  поправить день можно задним числом\n\n"
            "▸ заморозки\n"
            "  копятся за стрики: 7, 14, 30, 60, 100 дней 🧊\n\n"
            "начнём?"
        )
        await message.answer(guide, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())
        await state.clear()

    except ValueError:
        await message.answer(
            "напиши просто час цифрой от 0 до 23\nнапример: <code>14</code>",
            parse_mode=ParseMode.HTML
        )

# ==========================================
# ЧАСТЬ 3: МОИ ЧЕЛЛЕНДЖИ И УДАЛЕНИЕ
# ==========================================

@router.message(F.text == BTN_MY_CHALLENGES)
async def my_challenges_cmd(message: Message):
    async with async_session_maker() as session:
        # Автозавершение истёкших — side-effect осознанный, дешевле отдельного job
        await session.execute(
            update(Challenge)
            .where(and_(
                Challenge.target_date < date.today(),
                Challenge.status == ChallengeStatus.active
            ))
            .values(status=ChallengeStatus.completed, completed_at=date.today())
        )
        await session.commit()

        u = (await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )).scalar_one()

        res = await session.execute(
            select(Challenge).where(and_(
                Challenge.user_id == u.id,
                Challenge.status == ChallengeStatus.active
            ))
        )
        challenges = res.scalars().all()

        report = f"{get_user_rank(u.xp)}\n"
        kb_delete = InlineKeyboardMarkup(inline_keyboard=[])

        if not challenges:
            report += "\nактивных челленджей пока нет — жми «➕ новый»"
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
                eff = int(success_count / days_in * 100)
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
                Challenge.user_id == u.id,
                Challenge.status == ChallengeStatus.completed
            ))
        )).scalar()

        report += f"\nзавершено: {completed_count}   заморозок: {u.freeze_count}"

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
    kb_buttons.append([InlineKeyboardButton(text="❌ отмена", callback_data="close_settings")])
    await message.answer("какой челлендж запустим?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@router.callback_query(F.data == "new_custom")
async def start_custom_name(callback: CallbackQuery, state: FSMContext):
    #Отдельный handler для new_custom — зарегистрирован ДО startswith('new_').
    async with async_session_maker() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one()
        active_custom = await session.execute(
            select(func.count(Challenge.id)).where(and_(
                Challenge.user_id == user.id,
                Challenge.status == ChallengeStatus.active,
                Challenge.challenge_type.notin_(CHALLENGE_NAMES.keys())
            ))
        )
        if active_custom.scalar() >= 1:
            return await callback.answer(
                "в бесплатной версии доступен только 1 свой челлендж 🌟\n\n(подписка Plus в разработке)",
                show_alert=True
            )
    await callback.message.edit_text("как назовём твой челлендж?\nкоротко, до 30 символов:")
    await state.set_state(ChallengeState.waiting_for_custom_name)

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

@router.callback_query(F.data == "m_up", StateFilter(ChallengeState.selecting_mode))
async def save_streak_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
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

@router.callback_query(F.data == "m_down", StateFilter(ChallengeState.selecting_mode))
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
    #Сохраняет статус дня. При срыве (fail) — предлагает потратить заморозку. XP +10 начисляется только при первом success за день (не при повторном).
    _, cid, d_str, status = callback.data.split("_")
    d = datetime.strptime(d_str, "%d.%m.%Y").date()
    is_fin = False

    async with async_session_maker() as session:
        # При срыве — предложить заморозку если она есть
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

        # XP только если это новый success (не перезапись success→success)
        if status == DayStatus.success and (not day or day.status != DayStatus.success):
            u = (await session.execute(
                select(User).join(Challenge).where(Challenge.id == int(cid))
            )).scalar_one()
            u.xp += 10

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

        is_fin = bool(c.target_date and d == c.target_date and status == DayStatus.success)
        if is_fin:
            c.status = ChallengeStatus.completed
            c.completed_at = date.today()
            await session.commit()

        if status == DayStatus.success:
            await check_milestone(callback, new_streak, CHALLENGE_NAMES.get(c.challenge_type, ""), session)

    if is_fin:
        txt = f"🏆 <b>ПОЗДРАВЛЯЮ!</b> ты дошёл до цели!\n{CHALLENGE_NAMES.get(c.challenge_type, '')} завершён"
        img = "media/success.jpg"
    elif status == DayStatus.success:
        txt = "лучший! ещё один день в копилку твоей силы 🚀"
        img = "media/success.jpg"
    else:
        txt = "всё ровно, завтра — новый старт 🤝"
        img = "media/reset.jpg"

    await callback.message.delete()
    await send_with_image(callback, img, txt, reply_markup=main_menu_keyboard())

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
    #Каждую минуту проверяет: у каких юзеров сейчас наступило время отчёта. Флаг last_notified_at сравнивается с локальной датой юзера (user_today), а не серверной — корректно работает для разных часовых поясов. Каждые 30 XP отправляется мотивирующий совет. CHALLENGE_NAMES.get() защищает от KeyError для кастомных челленджей.
    async with async_session_maker() as session:
        now = datetime.now(timezone.utc)

        res = await session.execute(
            select(User).where(User.utc_offset.is_not(None))
        )
        for u in res.scalars():
            local_t   = now + timedelta(hours=u.utc_offset)
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

            # Мотивирующий совет каждые 30 XP
            if u.xp > 0 and u.xp % 30 == 0:
                try:
                    await bot.send_message(
                        u.telegram_id,
                        f"💡 <b>инсайт дня:</b>\n{random.choice(TIPS)}",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass

            for c in cs:
                # Не отправляем если день уже отмечен
                day_check = await session.execute(
                    select(ChallengeDay).where(and_(
                        ChallengeDay.challenge_id == c.id,
                        ChallengeDay.date == user_today
                    ))
                )
                if day_check.scalar_one_or_none():
                    continue
                # .get() — защита от KeyError для кастомных челленджей
                display_name = CHALLENGE_NAMES.get(c.challenge_type, c.challenge_type)
                try:
                    await bot.send_message(
                        u.telegram_id,
                        f"🔔 честный чек: <b>{display_name}</b>\nкак прошёл день?",
                        reply_markup=get_status_kb(c.id, user_today.strftime("%d.%m.%Y")),
                        disable_notification=u.silent_mode,
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    continue

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

@router.message()
async def fallback_echo(message: Message):
    #Ловит всё что не попало в другие хендлеры
    await message.answer(
        "я тебя не совсем понял 🧐\n"
        "нажми на кнопку или введи /cancel",
        reply_markup=main_menu_keyboard()
    )

# ==========================================
# ЧАСТЬ 8: ЗАПУСК
# ==========================================

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    scheduler = AsyncIOScheduler()
    # Каждую минуту — отправка уведомлений тем у кого наступило время
    scheduler.add_job(daily_task,     "interval", minutes=1, args=[bot])
    # Каждую минуту — auto-skip для тех у кого наступила полночь
    scheduler.add_job(auto_skip_task, "interval", minutes=1)
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