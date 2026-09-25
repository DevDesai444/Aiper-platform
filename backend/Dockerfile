FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY skills ./skills
COPY alembic.ini entrypoint.sh ./
COPY alembic ./alembic

RUN chmod +x entrypoint.sh \
 && mkdir -p /data/uploads \
 && groupadd --gid 10001 appgroup \
 && useradd --uid 10001 --gid appgroup --no-create-home appuser \
 && chown -R appuser:appgroup /data/uploads

USER appuser

EXPOSE 8000

# Migrate (alembic upgrade head), then exec uvicorn. verify_schema fails closed
# against an unmigrated database, so the upgrade must run before the API serves.
ENTRYPOINT ["./entrypoint.sh"]
