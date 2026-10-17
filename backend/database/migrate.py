"""Apply database/migrations/*.sql to the database in DATABASE_URL, in filename order.

Run from backend/:  uv run python -m database.migrate

Each file runs once, inside a transaction, and is recorded in public.schema_migrations.
Never edit a migration that teammates have already applied; add a new numbered file instead.
"""

import asyncio
from pathlib import Path

import asyncpg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def migrate() -> None:
    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migrations (
                filename text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        rows = await conn.fetch("SELECT filename FROM schema_migrations")
        applied = {row["filename"] for row in rows}
        pending = [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in applied]
        if not pending:
            print("Database is up to date.")
            return
        for path in pending:
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )
            print(f"Applied {path.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
