# app/db/database.py
import os
from typing import Optional
import logging
from dotenv import load_dotenv
from sqlalchemy.sql import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker

load_dotenv()
logger = logging.getLogger(__name__) # Use logger

DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

if DATABASE_URL is None:
    logger.critical("DATABASE_URL environment variable is not set.")
    raise ValueError("DATABASE_URL environment variable is not set.")
else:
    # Mask password in log
    log_db_url = DATABASE_URL
    if "@" in log_db_url:
        log_db_url = log_db_url.split('@')[0].split(':')[0] + ":********@" + log_db_url.split('@')[1]
    logger.info("Database URL configured: %s", log_db_url)


try:
    engine: AsyncEngine = create_async_engine(
        DATABASE_URL,
        echo=os.getenv("SQLALCHEMY_ECHO", "False").lower() == "true", # Control echo via env var
        pool_size=10, # Example pool size adjustment
        max_overflow=20 # Example overflow adjustment
    )
    # Use expire_on_commit=False if you need to access attributes of objects after commit
    async_session = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    logger.info("SQLAlchemy async engine and session maker created.")
except Exception as e:
    logger.exception("Failed to create SQLAlchemy engine or session maker: %s", e)
    raise # Re-raise the exception to prevent app startup

async def test_connection() -> None:
    """ Tests the database connection. """
    if not engine:
        logger.error("Database engine not initialized, cannot test connection.")
        return
    try:
        async with engine.connect() as conn: # Use connect() for a single test query
            result = await conn.execute(text("SELECT 1"))
            if result.scalar_one() == 1:
                logger.info("Database connected successfully!")
            else:
                logger.error("Database connection test failed (unexpected result).")
    except Exception as e:
        logger.error("Database connection failed: %s", e)
