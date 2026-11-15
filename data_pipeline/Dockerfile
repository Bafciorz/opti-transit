FROM ghcr.io/astral-sh/uv:bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY . .

RUN mkdir -p /app/data/archive /app/downloads

CMD ["sh", "-c", "python scripts/init_db.py && python runner.py"]
