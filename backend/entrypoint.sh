#!/usr/bin/env sh
# Container entrypoint: migrate, then serve.
#
# The schema is owned by Alembic. The API refuses to start against an
# unmigrated database (see app.db.base.verify_schema), so the upgrade has to
# succeed before uvicorn is exec'd.
set -eu

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
