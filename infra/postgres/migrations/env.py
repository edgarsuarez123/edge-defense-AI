import os
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from alembic import context

# Load .env from the project root (three levels up from this file:
# migrations/ -> postgres/ -> infra/ -> project root).
# load_dotenv() is a no-op if the file doesn't exist, so this is safe in CI
# where vars are injected as real environment variables instead.
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# Alembic Config object — gives access to values in alembic.ini
config = context.config

# Wire up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the sqlalchemy.url from the environment.
# DATABASE_SYNC_URL uses psycopg2 (sync driver) — Alembic requires synchronous connections.
# The app uses asyncpg at runtime; that is a separate URL (DATABASE_URL).
database_sync_url = os.environ.get("DATABASE_SYNC_URL")
if not database_sync_url:
    raise RuntimeError(
        "DATABASE_SYNC_URL environment variable is not set. "
        "Run migrations from within docker compose or with .env loaded."
    )
config.set_main_option("sqlalchemy.url", database_sync_url)

# target_metadata is None here because we write raw SQL in migrations.
# If we later switch to SQLAlchemy ORM models, point this at the metadata object.
target_metadata = None


def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection.
    Outputs raw SQL to stdout — useful for review, not for applying.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations against a live DB connection.
    This is the normal path: alembic upgrade head.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # NullPool: one connection per migration run, closed after
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
