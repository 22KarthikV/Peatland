# Build from the repo root: docker build -f api/Dockerfile .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/pyproject.toml
COPY pipeline/pyproject.toml pipeline/pyproject.toml
COPY ml/pyproject.toml ml/pyproject.toml
# Dependencies first for layer caching; only the api package is installed.
RUN mkdir -p api/src/api pipeline/src/pipeline ml/src/ml \
    && uv sync --frozen --no-dev --package peatwatch-api --no-install-workspace
COPY api/src api/src
COPY api/alembic api/alembic
COPY api/alembic.ini api/alembic.ini
RUN uv sync --frozen --no-dev --package peatwatch-api

RUN useradd --system --no-create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
