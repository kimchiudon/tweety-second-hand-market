FROM python:3.13.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS test

COPY pyproject.toml README.md LICENSE ./
COPY tiny_market ./tiny_market
COPY scripts ./scripts
COPY tests ./tests

RUN python -m pip install . \
    && python -m unittest discover -s tests -v

FROM base AS runtime

LABEL org.opencontainers.image.title="Tweety Second-hand Market" \
      org.opencontainers.image.source="https://github.com/kimchiudon/tweety-second-hand-market" \
      org.opencontainers.image.licenses="MIT"

RUN groupadd --system --gid 10001 tweety \
    && useradd --system --uid 10001 --gid tweety --home-dir /app --shell /usr/sbin/nologin tweety \
    && install -d -o tweety -g tweety -m 0750 /data

COPY pyproject.toml README.md LICENSE ./
COPY tiny_market ./tiny_market
COPY scripts ./scripts
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint

RUN python -m pip install . \
    && chmod 0555 /usr/local/bin/docker-entrypoint \
    && chown -R tweety:tweety /app

ENV PORT=8000 \
    TINY_MARKET_HOST=0.0.0.0 \
    TINY_MARKET_PORT=8000 \
    TINY_MARKET_DB=/data/market.db

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=3).read()"]

ENTRYPOINT ["docker-entrypoint"]
