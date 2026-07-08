# RankForge backend — FastAPI on uvicorn, deps via uv.
FROM python:3.13-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first (cache layer), then the package. README.md is required because
# pyproject.toml declares `readme = "README.md"`; uv builds the local package during
# sync and errors without it.
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --no-dev

COPY schema ./schema
# scripts/ ships the forward-only migration runner (apply_schema.py) that the
# rankforge deploy's one-shot `migrate` compose service runs.
COPY scripts ./scripts

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "rankforge_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
