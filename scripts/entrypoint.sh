#!/bin/sh
# Container start: migrate, seed (both idempotent; the seed is serialized by an advisory lock),
# then serve. Settings come from the environment (see README.md, "Konfiguration").
set -eu

# Each worker would pick its own random secret: sessions of one worker would fail on the others.
if [ "${WEB_CONCURRENCY:-1}" != "1" ] && [ -z "${SESSION_SECRET:-}" ]; then
    echo "SESSION_SECRET must be set when WEB_CONCURRENCY > 1" >&2
    exit 1
fi

alembic upgrade head
python -m app.db.seed

# Real client addresses behind a reverse proxy (login limits are per address): set
# FORWARDED_ALLOW_IPS to the proxy's address. The default trusts nobody's headers.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}" \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
