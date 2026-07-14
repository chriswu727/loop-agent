# syntax=docker/dockerfile:1.7
# =============================================================================
# FastAPI backend image. Multi-stage: a `dev` target with hot reload (used by
# docker-compose) and a small, non-root `runtime` target for production.
# Build context is the REPO ROOT:  docker build -f infra/docker/api.Dockerfile .
# =============================================================================
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"
WORKDIR /app

# ---- builder: install runtime dependencies into an isolated venv ----
FROM base AS builder
RUN python -m venv /opt/venv
COPY apps/api/requirements.lock /app/requirements.lock
RUN pip install --require-hashes -r requirements.lock

# ---- dev: editable install + dev tools + hot reload ----
FROM base AS dev
RUN python -m venv /opt/venv
COPY apps/api/pyproject.toml apps/api/README.md /app/
COPY apps/api/app /app/app
RUN pip install --upgrade pip && pip install -e ".[dev,office,calendar]"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- runtime: minimal, non-root, production ----
FROM base AS runtime
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
COPY apps/api/app /app/app
COPY apps/api/alembic /app/alembic
COPY apps/api/alembic.ini /app/alembic.ini
COPY apps/api/skills /app/skills
RUN chown -R app:app /app
USER app
EXPOSE 8000
# Horizontal scale = more pods. Keep workers modest per pod; the cluster scales out.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
