# API, worker and migration image (same code, different command).
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.12.7 && useradd --create-home --uid 10001 app
WORKDIR /app
COPY pyproject.toml uv.lock .python-version alembic.ini ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/mcp ./apps/mcp
COPY infrastructure/migrations ./infrastructure/migrations
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3
ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "oneledger_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
