FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini entrypoint.sh ./
COPY alembic ./alembic
COPY app ./app
COPY skills ./skills

RUN mkdir -p /data/uploads \
 && groupadd --gid 10001 appgroup \
 && useradd --uid 10001 --gid appgroup --no-create-home appuser \
 && chown -R appuser:appgroup /data/uploads

USER appuser

EXPOSE 8000

# entrypoint.sh: `alembic upgrade head`, then exec uvicorn. The schema is
# migration-owned; an image that boots the API without migrating is the bug
# this line closes.
ENTRYPOINT ["./entrypoint.sh"]
