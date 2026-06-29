FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

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

# Default to the production server (gunicorn via start-web.sh) so any deploy of
# this image is production-safe. Local development overrides this in
# docker-compose.yml (`command: python manage.py runserver`).
ENTRYPOINT ["/entrypoint.sh"]
CMD ["./start-web.sh"]
