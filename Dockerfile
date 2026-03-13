FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /config /media/movies /media/tv /share

COPY app/ ./app/
COPY tests/ ./tests/
COPY pytest.ini .

ENV RADARR_URL="" \
    RADARR_API_KEY="" \
    RADARR_ROOT_FOLDER="/movies" \
    RADARR_TAGS="" \
    SONARR_URL="" \
    SONARR_API_KEY="" \
    SONARR_ROOT_FOLDER="/tv" \
    SONARR_TAGS="" \
    BATCH_SIZE="20" \
    POLL_INTERVAL_MINUTES="15" \
    REQUIRE_APPROVAL="false" \
    MAX_CONCURRENT_COPIES="2" \
    MAX_SHARE_SIZE_GB="0" \
    MAX_SHARE_FILES="0" \
    SHARE_PATH="/share" \
    NTFY_URL="https://ntfy.sh" \
    NTFY_TOPIC="" \
    NTFY_TOKEN="" \
    TZ="UTC"

EXPOSE 8080

HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
