# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ---- deps layer ---------------------------------------------------------
# Install third-party deps before copying sources so dependency changes are
# cached.  Hatchling needs `app/` to exist for the editable install, so we
# materialise a stub here; the real code is copied in the runtime stage and
# overrides this stub.
FROM base AS deps
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py && \
    pip install --upgrade pip && \
    pip install -e ".[dev]"

# ---- runtime ------------------------------------------------------------
FROM deps AS runtime
COPY app ./app
COPY tests ./tests

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
