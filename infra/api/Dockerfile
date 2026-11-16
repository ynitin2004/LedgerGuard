# API image for local benchmarks and Linux-only dependencies (M2). Runs on Linux, where
# uvicorn's multi-worker mode and compiled wheels work (ADR-022). The production image
# (non-root, hardened, built in CI) is M10. Build context: repository root.
FROM ghcr.io/astral-sh/uv:0.11.29 AS uv
FROM python:3.12.10-slim-bookworm
COPY --from=uv /uv /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first (cached layer), runtime only: no dev or ml groups.
COPY pyproject.toml uv.lock ./
COPY packages/ledgerguard/pyproject.toml packages/ledgerguard/pyproject.toml
RUN uv sync --frozen --no-default-groups --no-install-workspace

COPY packages packages
COPY apps apps
COPY migrations migrations
COPY rules rules
COPY policy policy
RUN uv sync --frozen --no-default-groups

USER nobody
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD ["/opt/venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["/opt/venv/bin/python", "-m", "uvicorn", "--factory", "apps.api.main:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--no-access-log"]
