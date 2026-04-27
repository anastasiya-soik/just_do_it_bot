from aiogram.fsm.state import State, StatesGroup

class ChallengeState(StatesGroup):
    waiting_for_time = State()              # Настройка часового пояса при старте
    selecting_mode = State()                # Выбор режима (стрик или до даты)
    setting_date = State()                  # Ввод даты финиша
    history_selecting_challenge = State()   # Выбор челленджа для правки
    history_entering_date = State()         # Ввод даты для правки
    setting_report_time = State()           # Настройка времени отчетов
    setting_start_date = State()            # Ввод своей даты старта (миграция)
    waiting_for_custom_name = State()       # Ввод имени своего челленджа
    broadcast_text = State()               # Ввод текста рассылки (только для админа)