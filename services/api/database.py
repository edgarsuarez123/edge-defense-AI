"""
Async SQLAlchemy engine and session factory.

Why asyncpg and not psycopg2:
  FastAPI is async — blocking DB calls stall the event loop and kill concurrency.
  asyncpg is the fastest async PostgreSQL driver. SQLAlchemy's async layer wraps it
  cleanly with the same ORM/Core API we'd use in sync code.

Why create_async_engine at module level (not per-request):
  Connection pools are expensive. One engine per process, one pool shared across all
  requests. get_db() checks out a session from the pool per request and returns it on exit.
"""

import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

engine = create_async_engine(
    os.environ["DATABASE_URL"],
    pool_size=5,
    max_overflow=10,
    echo=False,
)

# expire_on_commit=False: after commit, attributes are still readable without a
# new SELECT. Critical for returning response bodies after write operations.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields one AsyncSession per request, closes on exit."""
    async with AsyncSessionLocal() as session:
        yield session
