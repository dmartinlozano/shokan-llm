FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 7860

CMD ["python", "src/main.py"]
