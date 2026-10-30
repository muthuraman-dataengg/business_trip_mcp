FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY copilot ./copilot
RUN pip install ".[ui]"

COPY ui ./ui
COPY scripts ./scripts
COPY .streamlit ./.streamlit

# Default policies are seeded into DATA_DIR automatically on first start.
ENV DATA_DIR=/app/data
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app /app
USER app

CMD ["python", "-m", "copilot.servers.travel", "--transport", "http", "--host", "0.0.0.0"]
