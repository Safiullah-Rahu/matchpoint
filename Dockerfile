# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MATCHPOINT_HOME=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[otel]" && useradd --create-home --uid 10001 matchpoint && mkdir -p /data && chown matchpoint /data

USER matchpoint
VOLUME ["/data"]
EXPOSE 8400

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8400/api/health', timeout=2)" || exit 1

# Seeds the synthetic ERP on first start. Set ANTHROPIC_API_KEY and pass --provider anthropic to run on Claude.
CMD ["matchpoint", "serve", "--host", "0.0.0.0", "--port", "8400"]
