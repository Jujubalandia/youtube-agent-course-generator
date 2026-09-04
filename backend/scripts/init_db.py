"""One-shot database initializer used by the ``backend-init`` compose service.

Upstream never calls ``Base.metadata.create_all`` (it only appears as a comment
in ``app/db/models.py``), so no ``courses`` table would exist and every save
would fail. This script connects to the configured ``DATABASE_URL`` (waiting
for Postgres to accept connections), then creates any missing tables.

Idempotent: ``create_all`` only adds tables that do not already exist, so it is
safe to re-run after restarts or volume restores.
"""

import asyncio
import logging
import os
import sys

# Allow `python scripts/init_db.py` to import the `app` package from the project
# root (the backend WORKDIR /srv/backend), even though running a script directly
# puts `scripts/` (not the project root) at the front of sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("init_db")

# Importing the models registers them on Base.metadata before create_all runs.
import app.db.models  # noqa: F401
from app.db.database import engine  # noqa: E402
from app.db.models import Base  # noqa: E402


async def _wait_for_db(retries: int = 30, delay: float = 2.0) -> None:
    """Poll the database until it accepts connections (compose starts the app
    as soon as postgres is healthy; this adds belt-and-braces retrying)."""
    from sqlalchemy import text

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database reachable (attempt %d/%d).", attempt, retries)
            return
        except Exception as exc:  # noqa: BLE001 - connection errors are expected pre-ready
            last_exc = exc
            logger.warning("Database not ready (attempt %d/%d): %s", attempt, retries, exc)
            await asyncio.sleep(delay)
    logger.critical("Could not connect to the database. DATABASE_URL=%s",
                    os.getenv("DATABASE_URL"))
    raise SystemExit(f"Database unavailable after {retries} attempts: {last_exc}") from last_exc


async def main() -> None:
    await _wait_for_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema check complete ('courses' table present if it did not exist).")
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
