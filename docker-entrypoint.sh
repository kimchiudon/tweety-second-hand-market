#!/bin/sh
set -eu

export TINY_MARKET_PORT="${PORT:-${TINY_MARKET_PORT:-8000}}"

python -m scripts.bootstrap_admin

exec gunicorn tiny_market.wsgi:application \
    --bind "0.0.0.0:${TINY_MARKET_PORT}" \
    --workers 1 \
    --threads 4 \
    --timeout 60 \
    --access-logfile -
