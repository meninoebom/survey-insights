# syntax=docker/dockerfile:1

# --- Build stage: install deps (cached on lockfiles), then the project. ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Deps layer: re-used whenever pyproject.toml / uv.lock are unchanged.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Project layer.
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- Runtime stage: small image, non-root, just the venv + source. ---
FROM python:3.12-slim-bookworm AS runtime
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app /app
ENV PATH="/app/.venv/bin:$PATH"
# The SOURCE_DIR mount point, owned by appuser so a fresh named volume inherits
# that ownership and the non-root process can seed and write uploads into it.
RUN mkdir -p /data && chown appuser:appuser /data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "survey.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
