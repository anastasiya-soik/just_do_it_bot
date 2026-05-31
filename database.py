import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from models import Base

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for col in (
            "custom_name VARCHAR(255)",
            "custom_emoji VARCHAR(255)",
            "report_time VARCHAR(8)",
            "attempt_number INTEGER DEFAULT 1",
            "best_attempt_streak INTEGER DEFAULT 0",
            "attempt_start_date DATE",
        ):
            try:
                await conn.execute(text(f"ALTER TABLE challenges ADD COLUMN IF NOT EXISTS {col}"))
            except Exception:
                pass
