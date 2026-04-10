from aiogram import Bot
from aiogram.types import (
    BotCommand, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from models import DayStatus

BTN_MY_CHALLENGES = "📊 мой прогресс"
BTN_NEW_CHALLENGE  = "➕ новый челлендж"
BTN_EDIT_HISTORY   = "📝 поправить день"
BTN_SETTINGS       = "⚙️ настройки"

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MY_CHALLENGES), KeyboardButton(text=BTN_NEW_CHALLENGE)],
            [KeyboardButton(text=BTN_EDIT_HISTORY),  KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
    )

def start_date_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="сегодня",                  callback_data="start_today")],
        [InlineKeyboardButton(text="выбрать свою дату старта", callback_data="start_custom")],
        [InlineKeyboardButton(text="❌ отмена",                callback_data="close_settings")],
    ])

def settings_keyboard(silent: bool, policy: DayStatus, time: str) -> InlineKeyboardMarkup:
    s_icon = "🔇" if silent else "🔊"
    policy_val = policy.value if hasattr(policy, "value") else policy
    p_icon = "⚠️ срыв" if policy_val == DayStatus.fail.value else "⏭ пропуск"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏰ отчёт в: {time}",      callback_data="set_time_prompt")],
        [InlineKeyboardButton(text="🌍 сменить пояс",           callback_data="set_tz_prompt")],
        [InlineKeyboardButton(text=f"{s_icon} без звука",      callback_data="toggle_silent")],
        [InlineKeyboardButton(text=f"🔄 если забыл: {p_icon}", callback_data="toggle_policy")],
        [InlineKeyboardButton(text="❌ закрыть",                callback_data="close_settings")],
    ])

async def set_main_menu(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="/start",  description="меню"),
        BotCommand(command="/cancel", description="отмена"),
        BotCommand(command="/help",   description="инфо"),
    ])