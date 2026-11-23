# Backend API image. Demo mode (default) replays the committed, sanitized cassettes in
# tests/fixtures, so the image runs with no credentials and makes no NYC API calls.
# Set DATA_MODE=live to query NYC Open Data instead.
FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.11.32

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    DATA_MODE=demo \
    LLM_ENABLED=0

WORKDIR /app
COPY pyproject.toml uv.lock README.md* ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
# Demo data: the sanitized cassettes only (other test files are not needed at runtime).
COPY tests/fixtures ./tests/fixtures
RUN uv sync --locked --no-dev

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health')"
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn nyc_apartment_agent.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
