"""Engine for the app store."""

from sqlalchemy import Engine, create_engine

from planlayer.config import Settings
from planlayer.store.schema import APP_SCHEMA


def create_app_engine(settings: Settings) -> Engine:
    """Engine for ``planlayer_app``, with the symbolic schema mapped to the configured one."""
    engine = create_engine(settings.app_url, pool_pre_ping=True)
    return engine.execution_options(schema_translate_map={APP_SCHEMA: settings.app_schema})
