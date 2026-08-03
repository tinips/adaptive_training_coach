FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG INSTALL_DEV=false

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN if [ "$INSTALL_DEV" = "true" ]; then \
        python -m pip install ".[dev]"; \
    else \
        python -m pip install .; \
    fi

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
