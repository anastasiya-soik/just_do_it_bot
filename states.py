from aiogram.fsm.state import State, StatesGroup

class ChallengeState(StatesGroup):
    waiting_for_time = State()
    selecting_mode = State()
    setting_date = State()
    history_selecting_challenge = State()
    history_entering_date = State()
    setting_report_time = State()
    setting_start_date = State()