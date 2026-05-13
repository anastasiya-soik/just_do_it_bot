"""Unit tests for pure functions in main.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock

with patch.dict(os.environ, {
    "BOT_TOKEN": "123:fake",
    "DATABASE_URL": "postgresql://fake:fake@localhost/fake",
    "REDIS_URL": "redis://localhost:6379",
}):
    with patch("aiogram.Bot.__init__", return_value=None):
        with patch("database.engine", MagicMock()):
            with patch("database.async_session_maker", MagicMock()):
                import main as m


# ── plural ────────────────────────────────────────────────────────────────────

def test_plural_one():       assert m.plural(1,  "заморозка", "заморозки", "заморозок") == "заморозка"
def test_plural_two():       assert m.plural(2,  "заморозка", "заморозки", "заморозок") == "заморозки"
def test_plural_five():      assert m.plural(5,  "заморозка", "заморозки", "заморозок") == "заморозок"
def test_plural_eleven():    assert m.plural(11, "заморозка", "заморозки", "заморозок") == "заморозок"
def test_plural_twenty_one():assert m.plural(21, "заморозка", "заморозки", "заморозок") == "заморозка"
def test_plural_hundred():   assert m.plural(100,"заморозка", "заморозки", "заморозок") == "заморозок"


# ── plural_days ───────────────────────────────────────────────────────────────

def test_plural_days_1():    assert m.plural_days(1)   == "день"
def test_plural_days_2():    assert m.plural_days(2)   == "дня"
def test_plural_days_4():    assert m.plural_days(4)   == "дня"
def test_plural_days_5():    assert m.plural_days(5)   == "дней"
def test_plural_days_11():   assert m.plural_days(11)  == "дней"
def test_plural_days_21():   assert m.plural_days(21)  == "день"
def test_plural_days_100():  assert m.plural_days(100) == "дней"
def test_plural_days_101():  assert m.plural_days(101) == "день"


# ── get_progress_bar ──────────────────────────────────────────────────────────

def test_progress_bar_zero():
    bar = m.get_progress_bar(0)
    assert "0%" in bar

def test_progress_bar_100():
    bar = m.get_progress_bar(100)
    assert "100%" in bar

def test_progress_bar_50():
    bar = m.get_progress_bar(50)
    assert "50%" in bar

def test_progress_bar_clamps_below_zero():
    assert "0%" in m.get_progress_bar(-10)

def test_progress_bar_clamps_above_100():
    assert "100%" in m.get_progress_bar(150)

def test_progress_bar_segment_length():
    bar = m.get_progress_bar(33)
    segment = bar.split(" ")[0]
    assert len(segment) == 8


# ── build_check_kb ────────────────────────────────────────────────────────────

def test_check_kb_has_two_buttons():
    kb = m.build_check_kb(42, "29.04.2026")
    assert len(kb.inline_keyboard[0]) == 2

def test_check_kb_success_callback():
    kb = m.build_check_kb(42, "29.04.2026")
    callbacks = [b.callback_data for b in kb.inline_keyboard[0]]
    assert "save_42_29.04.2026_success" in callbacks

def test_check_kb_fail_callback():
    kb = m.build_check_kb(42, "29.04.2026")
    callbacks = [b.callback_data for b in kb.inline_keyboard[0]]
    assert "save_42_29.04.2026_fail" in callbacks

def test_check_kb_different_challenges():
    cb1 = m.build_check_kb(1, "01.01.2026").inline_keyboard[0][0].callback_data
    cb2 = m.build_check_kb(2, "01.01.2026").inline_keyboard[0][0].callback_data
    assert cb1 != cb2


# ── extract_emoji ─────────────────────────────────────────────────────────────

def _msg(text):
    msg = MagicMock()
    msg.text = text
    msg.entities = None
    return msg

def test_extract_emoji_simple():
    assert m.extract_emoji(_msg("😎")) == "😎"

def test_extract_emoji_flag_full():
    # 🇬🇧 = два символа regional indicator, оба должны вернуться
    result = m.extract_emoji(_msg("🇬🇧"))
    assert result == "🇬🇧"

def test_extract_emoji_flag_not_cut():
    # раньше emoji[0] срезал второй символ флага
    result = m.extract_emoji(_msg("🇬🇧"))
    assert len(result) == 2

def test_extract_emoji_no_emoji_returns_empty():
    assert m.extract_emoji(_msg("пупа")) == ""

def test_extract_emoji_empty_text():
    assert m.extract_emoji(_msg("")) == ""

def test_extract_emoji_none_text():
    assert m.extract_emoji(_msg(None)) == ""

def test_extract_emoji_emoji_then_text():
    # берём только эмодзи, не весь текст
    result = m.extract_emoji(_msg("😎 текст"))
    assert result == "😎"
    assert "текст" not in result

def test_extract_emoji_multiple_emoji_takes_all_leading():
    result = m.extract_emoji(_msg("👉😎 привет"))
    assert "👉" in result
    assert "😎" in result
    assert "привет" not in result


# ── get_challenge_name ────────────────────────────────────────────────────────

def _challenge(ctype, custom_name=None, custom_emoji=None):
    c = MagicMock()
    c.challenge_type = ctype
    c.custom_name = custom_name
    c.custom_emoji = custom_emoji
    return c

def test_challenge_name_predefined():
    assert m.get_challenge_name(_challenge("no_alcohol")) == "🍷 алко-пауза"

def test_challenge_name_predefined_sugar():
    assert m.get_challenge_name(_challenge("no_sugar")) == "🍰 без сладкого"

def test_challenge_name_custom_with_emoji():
    c = _challenge("custom", custom_name="пупа", custom_emoji="😎")
    assert m.get_challenge_name(c) == "😎 пупа"

def test_challenge_name_custom_no_emoji_uses_default():
    c = _challenge("custom", custom_name="пупа", custom_emoji=None)
    result = m.get_challenge_name(c)
    assert "пупа" in result
    assert "🎯" in result

def test_challenge_name_custom_no_name_uses_default():
    c = _challenge("custom", custom_name=None, custom_emoji="😎")
    result = m.get_challenge_name(c)
    assert "😎" in result

def test_challenge_name_unknown_type_returns_type():
    c = _challenge("unknown_type")
    assert m.get_challenge_name(c) == "unknown_type"


# ── Premium — константы и логика ──────────────────────────────────────────────

def test_stars_custom_price_is_100():
    assert m.STARS_CUSTOM_PRICE == 100

def test_stars_freeze_1_is_15():
    assert m.STARS_FREEZE_1 == 15

def test_stars_freeze_3_is_30():
    assert m.STARS_FREEZE_3 == 30

def test_freeze_3_cheaper_per_unit():
    # 3 заморозки за 30 звёзд выгоднее чем 3×15=45
    assert m.STARS_FREEZE_3 < m.STARS_FREEZE_1 * 3

def test_premium_payloads_defined():
    # payment_done разбирает эти строки — если изменятся, тест напомнит
    source = open("main.py", encoding="utf-8").read()
    assert '"custom_unlimited"' in source
    assert '"freeze_1"'         in source
    assert '"freeze_3"'         in source

def test_premium_free_limit_is_one():
    # бесплатный лимит кастомных — 1 штука (active_custom >= 1 → пейвол)
    source = open("main.py", encoding="utf-8").read()
    assert "active_custom >= 1" in source

def test_admin_id_from_env():
    # ADMIN_ID должен читаться из env, не быть захардкожен
    assert m.ADMIN_ID == 0  # 0 — дефолт когда env не задан

def test_plural_freeze_after_payment_1():
    # +1 заморозка: freeze_count=1 → "заморозка"
    assert m.plural(1, "заморозка", "заморозки", "заморозок") == "заморозка"

def test_plural_freeze_after_payment_3():
    # +3 заморозки: freeze_count=3 → "заморозки"
    assert m.plural(3, "заморозка", "заморозки", "заморозок") == "заморозки"


# ── get_status_kb ─────────────────────────────────────────────────────────────

def test_status_kb_has_three_rows():
    kb = m.get_status_kb(7, "01.05.2026")
    assert len(kb.inline_keyboard) == 3

def test_status_kb_success_callback():
    kb = m.get_status_kb(7, "01.05.2026")
    callbacks = [b.callback_data for b in kb.inline_keyboard[0]]
    assert "save_7_01.05.2026_success" in callbacks

def test_status_kb_fail_callback():
    kb = m.get_status_kb(7, "01.05.2026")
    callbacks = [b.callback_data for b in kb.inline_keyboard[0]]
    assert "save_7_01.05.2026_fail" in callbacks

def test_status_kb_skip_callback():
    kb = m.get_status_kb(7, "01.05.2026")
    assert kb.inline_keyboard[1][0].callback_data == "save_7_01.05.2026_skip"

def test_status_kb_close_callback():
    kb = m.get_status_kb(7, "01.05.2026")
    assert kb.inline_keyboard[2][0].callback_data == "close_settings"

def test_status_kb_different_challenges():
    cb1 = m.get_status_kb(1, "01.01.2026").inline_keyboard[0][0].callback_data
    cb2 = m.get_status_kb(2, "01.01.2026").inline_keyboard[0][0].callback_data
    assert cb1 != cb2


# ── quick_kb ──────────────────────────────────────────────────────────────────

def test_quick_kb_single_button():
    kb = m.quick_kb(("текст", "cb_data"))
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].text == "текст"
    assert kb.inline_keyboard[0][0].callback_data == "cb_data"

def test_quick_kb_multiple_buttons_each_in_own_row():
    kb = m.quick_kb(("а", "cb_a"), ("б", "cb_b"), ("в", "cb_c"))
    assert len(kb.inline_keyboard) == 3

def test_quick_kb_callbacks_match():
    kb = m.quick_kb(("один", "c1"), ("два", "c2"))
    assert kb.inline_keyboard[0][0].callback_data == "c1"
    assert kb.inline_keyboard[1][0].callback_data == "c2"

def test_quick_kb_empty_returns_empty():
    kb = m.quick_kb()
    assert kb.inline_keyboard == []
