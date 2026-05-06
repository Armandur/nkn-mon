# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

FROM base AS dev
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -e ".[dev]" || \
    uv pip install --system --no-cache \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "httpx>=0.27" "pydantic>=2.9" \
      "pyyaml>=6.0" "pytest>=8" "pytest-asyncio>=0.24" "ruff>=0.7"
COPY src ./src
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS production
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache .
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
