FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Fail safe to production (never default to DEBUG=True). Local dev overrides via compose.
ENV DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY . .
RUN chmod +x start-web.sh

# Collect static at build, not per cold start. Throwaway env satisfies the
# settings import; collectstatic hits no DB.
RUN SECRET_KEY=build-only-not-used \
    DATABASE_URL=postgres://build:build@localhost:5432/build \
    python manage.py collectstatic --noinput

# Default to the production server; local dev overrides via docker-compose command.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["./start-web.sh"]
