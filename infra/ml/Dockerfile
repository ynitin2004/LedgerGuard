# Linux image for model training (scripts/train_model.py). Used where numpy cannot run
# natively, e.g. the primary Windows dev PC (ADR-021). Both parts are pinned.
FROM ghcr.io/astral-sh/uv:0.11.29 AS uv
FROM python:3.12.10-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /repo
