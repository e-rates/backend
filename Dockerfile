# GeoDjango needs GDAL/GEOS/PROJ, which is why this is not a slim-only build.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN apt-get update && apt-get install --no-install-recommends -y \
        binutils \
        libproj-dev \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# Dependencies first so code edits don't invalidate the install layer.
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-interaction

COPY . .

# Collected at build time so the image can serve static without a writable volume.
# A throwaway key is fine here: nothing secret is baked in, the real one comes from the environment.
RUN SECRET_KEY=build-only FIELD_ENCRYPTION_KEY=jm9NLCGmZ1nRRVYzUqMHhTcUtHpBm4dUJXLPwFMrcVw= \
    DB_PASSWORD=build-only DEBUG=True \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 erates \
    && chown -R erates:erates /app
USER erates

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health/ || exit 1

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
