from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db.base import Base

# Import model modules so Alembic detects all model metadata.
#
# Every module holding a mapped class must appear here. A model that is not
# imported is absent from Base.metadata, and autogenerate would then read the
# live table as "not in the models" and emit a drop_table for it.
from app.db.models import (
    business,
    membership,
    dataset,
    customer,
    product,
    order,
    inventory,
    analytics,
    events,
)  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# -------------------------------------------------------------------
# Database URL
# -------------------------------------------------------------------
# The application uses psycopg v3.
# Explicitly tell Alembic/SQLAlchemy to use the same driver.
if settings.database_url:
    database_url = settings.database_url

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    elif database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )

    # Alembic uses ConfigParser, where % has special meaning.
    database_url = database_url.replace("%", "%%")

    config.set_main_option(
        "sqlalchemy.url",
        database_url,
    )


target_metadata = Base.metadata


# -------------------------------------------------------------------
# Offline migrations
# -------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# -------------------------------------------------------------------
# Online migrations
# -------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()