# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[postgres]"

COPY alembic.ini ./
COPY alembic ./alembic

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 ledger
USER ledger

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health').status == 200 else 1)"
# Apply migrations, then serve.
CMD ["sh", "-c", "alembic upgrade head && uvicorn 'testledger.api:create_app' --factory --host 0.0.0.0 --port 8000"]
