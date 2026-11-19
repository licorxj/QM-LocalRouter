"""Alembic migration environment for ApiRouteManeger.

The application runtime uses the async engine (`sqlite+aiosqlite`) defined in
`app.database`; Alembic needs a synchronous connection, so the URL is derived
from `settings.DATABASE_URL` with the driver swapped to plain `sqlite`. The
database file itself is the same one the app uses.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Ensure the backend package root is importable both in source and PyInstaller
# frozen builds (script_location is bundled and mapper metadata lives in app.*).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

# Import every model so all tables are registered on Base.metadata before
# autogenerate compares it against the live database. Conversation is not
# exported from app.models.__all__, so import it explicitly.
from app import models  # noqa: F401,E402
from app.models.conversation import Conversation  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Build a synchronous SQLAlchemy URL pointing at the same database file.

    Prefer the URL injected via ``alembic config`` (set by
    ``app.database._make_alembic_config`` and by CLI/test overrides); fall back
    to ``settings.DATABASE_URL`` when alembic.ini leaves it blank.
    """
    url = config.get_main_option("sqlalchemy.url", "") or settings.DATABASE_URL
    return url.replace("sqlite+aiosqlite", "sqlite")


def run_migrations_offline() -> None:
    """Emit SQL scripts without connecting (alembic upgrade --sql)."""
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live database using a synchronous engine."""
    connectable = create_engine(_sync_url(), poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
