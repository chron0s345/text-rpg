# App image (ARCHITECTURE.md, "Deployment"): uvicorn serving app.main:app.
# On start the container applies migrations and seeds missing world state (both idempotent).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/text-rpg

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY data ./data
COPY app ./app
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh

RUN useradd --system --no-create-home rpg && chmod +x scripts/entrypoint.sh
USER rpg

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

ENTRYPOINT ["./scripts/entrypoint.sh"]
