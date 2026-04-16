from __future__ import with_statement

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from db.base import Base
from db import models  # noqa: F401 - import registers models


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _set_url_from_env() -> None:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        config.set_main_option("sqlalchemy.url", url)


def run_migrations_offline() -> None:
    _set_url_from_env()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _set_url_from_env()
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
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

