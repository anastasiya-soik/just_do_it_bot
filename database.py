import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from models import Base

# --- ЭТО ВАЖНОЕ ИЗМЕНЕНИЕ ---
# Ищем переменную окружения 'DATA_DIR'. Если ее нет (локальный запуск), используем папку 'data'.
# На сервере мы укажем, что DATA_DIR - это /data.
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(exist_ok=True)  # Создаем папку, если ее нет

DATABASE_PATH = DATA_DIR / "database.sqlite"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH.as_posix()}"
# --- КОНЕЦ ИЗМЕНЕНИЙ ---

engine = create_async_engine(DATABASE_URL, echo=False) 
async_session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Manual migrations for columns added after initial deploy
        for sql in [
            "ALTER TABLE challenges ADD COLUMN partner_challenge_id INTEGER REFERENCES challenges(id) ON DELETE SET NULL",
        ]:
            try:
                await conn.exec_driver_sql(sql)
            except Exception:
                pass  # column already exists