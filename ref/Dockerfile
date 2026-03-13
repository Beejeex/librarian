FROM python:3.12-slim

# Install curl for the Docker health check
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-create mount point directories so the container works without volumes attached
# /media/movies and /media/tv are the expected subdirectory structure
RUN mkdir -p /config /media/movies /media/tv /share

# Install Python dependencies first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and tests
COPY app/ ./app/
COPY tests/ ./tests/
COPY pytest.ini .

EXPOSE 8080

# Confirm the app is responding before marking the container healthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
