# Backend image shared by the api, worker and migrate services (modular monolith: one codebase,
# several process entrypoints). Build context is the repository root.

ARG PYTHON_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.21

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /workspace

# Dependencies first so source edits do not invalidate the dependency layer.
COPY pyproject.toml uv.lock ./
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
COPY packages/product-config/pyproject.toml packages/product-config/
RUN uv sync --locked --no-dev --all-packages --no-install-workspace

COPY packages/product-config/ packages/product-config/
COPY services/api/ services/api/
COPY services/worker/ services/worker/
# Non-editable install: the runtime image does not depend on the source tree layout.
RUN uv sync --locked --no-dev --all-packages --no-editable

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY services/api/alembic.ini ./alembic.ini
USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "tapjob_api.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
