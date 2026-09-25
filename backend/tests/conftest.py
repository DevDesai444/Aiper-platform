"""Global test fixtures.

Sets required environment variables *before* any app code is imported so that
the cached `get_settings()` singleton picks them up.
"""

import os

# Enable dev mode so startup secret checks are skipped.
os.environ.setdefault("AIPER_DEV_MODE", "1")
# Provide a plausible DATABASE_URL — the smoke test doesn't connect, but
# `create_async_engine` requires a non-empty string at import time.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://aiper_user:aiper_password@localhost:5432/aiper_db",
)
