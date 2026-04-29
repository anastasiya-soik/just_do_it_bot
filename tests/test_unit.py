"""Unit tests for pure functions in main.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock

# Patch env/bot imports before importing main
with patch.dict(os.environ, {"BOT_TOKEN": "123:fake", "DATA_DIR": "/tmp/test_bot_data"}):
    with patch("aiogram.Bot.__init__", return_value=None):
        with patch("database.engine", MagicMock()):
            with patch("database.async_session_maker", MagicMock()):
                import main as m


# ── plural_days ──────────────────────────────────────────────────────────────

def test_plural_days_1():
    assert m.plural_days(1) == "день"

def test_plural_days_2():
    assert m.plural_days(2) == "дня"

def test_plural_days_3():
    assert m.plural_days(3) == "дня"

def test_plural_days_4():
    assert m.plural_days(4) == "дня"

def test_plural_days_5():
    assert m.plural_days(5) == "дней"

def test_plural_days_11():
    assert m.plural_days(11) == "дней"

def test_plural_days_21():
    assert m.plural_days(21) == "день"

def test_plural_days_100():
    assert m.plural_days(100) == "дней"


# ── get_user_rank ─────────────────────────────────────────────────────────────

def test_rank_zero_xp():
    result = m.get_user_rank(0)
    assert "только вылупился" in result

def test_rank_50_xp():
    result = m.get_user_rank(50)
    assert "что-то начинается" in result

def test_rank_200_xp():
    result = m.get_user_rank(200)
    assert "серьёзный человек" in result

def test_rank_500_xp():
    result = m.get_user_rank(500)
    assert "машина без срывов" in result

def test_rank_1000_xp():
    result = m.get_user_rank(1000)
    assert "страшный сон" in result

def test_rank_shows_xp_value():
    result = m.get_user_rank(75)
    assert "75" in result

def test_rank_shows_next_rank_gap():
    # at 50 xp, next rank at 200 → gap = 150
    result = m.get_user_rank(50)
    assert "150" in result

def test_rank_max_no_next():
    # at 1000+ xp there's no next rank → no arrow
    result = m.get_user_rank(9999)
    assert "→" not in result


# ── get_progress_bar ──────────────────────────────────────────────────────────

def test_progress_bar_zero():
    bar = m.get_progress_bar(0)
    assert bar.startswith("░░░░░░░░")
    assert "0%" in bar

def test_progress_bar_100():
    bar = m.get_progress_bar(100)
    assert bar.startswith("████████")
    assert "100%" in bar

def test_progress_bar_50():
    bar = m.get_progress_bar(50)
    assert "████" in bar
    assert "50%" in bar

def test_progress_bar_clamps_below_zero():
    bar = m.get_progress_bar(-10)
    assert "0%" in bar

def test_progress_bar_clamps_above_100():
    bar = m.get_progress_bar(150)
    assert "100%" in bar

def test_progress_bar_length():
    # bar segment is always 8 chars + space + percent
    bar = m.get_progress_bar(33)
    segment = bar.split(" ")[0]
    assert len(segment) == 8


# ── build_check_kb ────────────────────────────────────────────────────────────

def test_check_kb_has_two_buttons():
    kb = m.build_check_kb(42, "29.04.2026")
    buttons = kb.inline_keyboard[0]
    assert len(buttons) == 2

def test_check_kb_success_callback():
    kb = m.build_check_kb(42, "29.04.2026")
    cb_data = [b.callback_data for b in kb.inline_keyboard[0]]
    assert "save_42_29.04.2026_success" in cb_data

def test_check_kb_fail_callback():
    kb = m.build_check_kb(42, "29.04.2026")
    cb_data = [b.callback_data for b in kb.inline_keyboard[0]]
    assert "save_42_29.04.2026_fail" in cb_data

def test_check_kb_no_skip_button():
    kb = m.build_check_kb(42, "29.04.2026")
    cb_data = [b.callback_data for b in kb.inline_keyboard[0]]
    assert not any("skip" in d for d in cb_data)

def test_check_kb_button_text_friendly():
    kb = m.build_check_kb(1, "01.01.2026")
    texts = [b.text for b in kb.inline_keyboard[0]]
    # both buttons should be lowercase (no uppercase letters)
    for t in texts:
        assert t == t.lower() or any(c in t for c in "✅😔"), f"unexpected casing: {t}"

def test_check_kb_different_challenges_different_callbacks():
    kb1 = m.build_check_kb(1, "01.01.2026")
    kb2 = m.build_check_kb(2, "01.01.2026")
    cb1 = kb1.inline_keyboard[0][0].callback_data
    cb2 = kb2.inline_keyboard[0][0].callback_data
    assert cb1 != cb2
