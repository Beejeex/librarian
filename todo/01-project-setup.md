# TODO 01 — Project Setup & Docker

## Goal
Create the skeleton project structure: Dockerfile, requirements, pytest config, app package, and static JS assets. The container should build and start (returning 200 on `/health`) before any feature modules are written.

---

## Tasks

### 1.1 — Directory structure
Create all required directories and empty `__init__.py` files:
```
app/
app/routers/
app/static/js/
app/templates/
tests/
```

### 1.2 — requirements.txt
Pin all dependencies:
```
fastapi
uvicorn[standard]
sqlmodel
httpx
sse-starlette
jinja2
pytest
pytest-asyncio
respx
```

### 1.3 — Dockerfile
```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /config /media/movies /media/tv

COPY app/ ./app/

EXPOSE 8080

HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 1.4 — pytest.ini
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

### 1.5 — app/main.py (skeleton)
- Create FastAPI app with `lifespan` context manager.
- Lifespan: create DB tables on startup, dispose engine on shutdown.
- Register `ui.py` and `api.py` routers.
- Mount `/static` directory.
- Register Jinja2 templates.

### 1.6 — Self-hosted JS assets
Download and place in `app/static/js/`:
- `htmx.min.js` (htmx 1.9.x or 2.x latest stable)
- `alpine.min.js` (Alpine.js 3.x latest stable)

These must be committed to the repo — no CDN dependencies at runtime.

### 1.7 — Smoke test
Verify:
```powershell
docker build -t librarian .
docker run --rm -p 8080:8080 librarian
# curl http://localhost:8080/health → {"status": "ok"}
```

---

## Acceptance Criteria
- [ ] `docker build -t librarian .` succeeds
- [ ] `docker run --rm librarian pytest -v` runs (no tests yet, just confirms setup)
- [ ] `GET /health` returns `{"status": "ok"}` with HTTP 200
- [ ] htmx.min.js and alpine.min.js are served correctly at `/static/js/`
