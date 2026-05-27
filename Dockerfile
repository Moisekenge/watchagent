# Single image, two roles: the `api` and `poller` services run it with
# different commands (see docker-compose.yml). Builds with no API keys.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/code

WORKDIR /code

# Dependencies first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code, plus the Cursor skills so they can run via
# `docker compose exec api python .cursor/skills/.../*.py`.
COPY app ./app
COPY .cursor ./.cursor

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /code
USER appuser

EXPOSE 8000

# Default command is the API; the poller service overrides it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
