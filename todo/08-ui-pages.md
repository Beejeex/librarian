# TODO 08 — UI Pages & Templates

## Goal
Implement `app/routers/ui.py` and all Jinja2 templates. The UI is server-rendered; HTMX handles partial updates (approve/skip buttons); Alpine.js handles client-side toggles (Approve All confirmation, batch size input). No custom JS files — all interactivity via HTMX attributes and Alpine.js.

---

## Design System

- **Nav bar**: dark background `#1e293b`, white text, app name "Librarian" on the left.
- **Page body**: light grey `#f1f5f9`.
- **Cards**: white `#ffffff`, subtle box-shadow, 8px border-radius.
- **Status badges**:
  - `pending` = grey
  - `approved` = blue
  - `skipped` = yellow
  - `done` = green
  - `error` = red
- **No external CSS framework** — all styles inlined in `base.html` or element `style` attributes.
- Nav links: Dashboard (`/`), Review (`/review`), Settings (`/settings`), Logs (`/logs`).

---

## Tasks

### 8.1 — app/templates/base.html

Base layout shared by all pages:
- `<head>`: charset, viewport, title block, inline `<style>` for global styles.
- Loads `htmx.min.js` and `alpine.min.js` from `/static/js/`.
- Dark nav bar with app name and nav links.
- `{% block content %}` in main body.
- No CDN links anywhere.

### 8.2 — Dashboard — app/templates/dashboard.html

Route: `GET /`

Content:
- Page heading: "Librarian"
- Short description: "Standardise your Radarr and Sonarr library folder names."
- **Source picker**: two large clickable cards — Radarr and Sonarr. Clicking a card POSTs to `/api/scan?source=radarr` (or sonarr) via an HTMX or plain form submit.
- **Last scan summary card** (if a recent ScanRun exists):
  - Source, timestamp, total items found, done count, error count.
  - Status badge.
  - "View Review" link if status is `ready`.
- If no scan has been run yet: "No scans yet. Pick a source above to start."

### 8.3 — Review Page — app/templates/review.html

Route: `GET /review`

Content:
- Heading: "Review — [Source] scan"
- **Stats bar**: total pending / approved / skipped counts.
- **Batch size input**: `<input type="number" name="batch_size" value="20" min="1" max="500">` (stored in form, posted with Apply).
- **Approve All button**: POSTs to `/api/items/approve-all?scan_run_id=X` via HTMX or plain form; updates the page.
- **Items table**:

  | Col | Content |
  |---|---|
  | Title | `item.title` |
  | Current Folder | `item.current_folder` (monospace) |
  | → | arrow |
  | Expected Folder | `item.expected_folder` (monospace) |
  | Status | Badge (pending / approved / skipped) |
  | Actions | Approve button / Skip button |

  - **Approve button**: `hx-post="/api/items/{id}/approve"`, `hx-swap="outerHTML"` on the row (or just the status cell).
  - **Skip button**: `hx-post="/api/items/{id}/skip"`, same swap.
  - Already-approved rows show a green "Approved" badge; skip button still available.
  - Already-skipped rows show a yellow "Skipped" badge; approve button still available.

- **Apply button**: form POST to `/api/apply` with `scan_run_id` and `batch_size` as hidden inputs.
- If no items: "All folders already match the naming template." with a "Scan Again" link.

### 8.4 — Apply / Logs Page — app/templates/logs.html

Route: `GET /apply`

Content:
- Heading: "Applying..."
- **Live log container**: `<pre id="log">` that auto-scrolls.
- **SSE connection** via HTMX SSE extension:
  ```html
  <div hx-ext="sse" sse-connect="/api/stream"
       sse-swap="message" hx-target="#log" hx-swap="beforeend">
  ```
  Or use Alpine.js `EventSource` and append lines to a reactive array.
- **Auto-scroll**: Alpine.js `x-data` + `x-effect` watching the log content and scrolling `pre` to bottom.
- **Done state**: when `[DONE]` event received, swap heading to "Done" and show summary link back to dashboard.

### 8.5 — Settings Page — app/templates/settings.html

Route: `GET /settings`

Content:
- **Radarr section**:
  - URL input: `radarr_url`
  - API Key input (type=password): `radarr_api_key`
  - Root Folder input: `radarr_root_folder` (default `/movies`)
- **Sonarr section**:
  - Same three fields for Sonarr.
- **General section**:
  - Batch Size: `batch_size` number input.
- **Save button**: POST to `/api/settings` (form submit).
- On save: flash "Settings saved." confirmation message (HTMX OOB swap or Alpine.js reactive).

### 8.6 — Logs/History Page — app/templates/logs.html (reuse or separate)

Route: `GET /logs`

Content:
- Recent log lines from `log_buffer.tail(200)`.
- "Clear" button: `hx-post="/api/logs/clear"`, clears the in-memory buffer and refreshes the page content.

### 8.7 — app/routers/ui.py

```python
@router.get("/")
async def dashboard(request: Request, session: Session = Depends(get_session)):
    latest_radarr = # latest ScanRun for radarr
    latest_sonarr = # latest ScanRun for sonarr
    return templates.TemplateResponse("dashboard.html", {...})

@router.get("/review")
async def review(request: Request, session: Session = Depends(get_session)):
    # Load latest ready ScanRun, all its RenameItems
    return templates.TemplateResponse("review.html", {...})

@router.get("/apply")
async def apply_page(request: Request):
    return templates.TemplateResponse("logs.html", {...})

@router.get("/settings")
async def settings_page(request: Request, session: Session = Depends(get_session)):
    config = get_config(session)
    return templates.TemplateResponse("settings.html", {"config": config, ...})

@router.post("/settings")
async def save_settings(request: Request, session: Session = Depends(get_session), ...):
    save_config(session, form_data)
    return RedirectResponse("/settings", status_code=303)

@router.get("/logs")
async def logs_page(request: Request):
    lines = log_buffer.tail(200)
    return templates.TemplateResponse("logs.html", {"lines": lines, ...})
```

---

## Acceptance Criteria
- [ ] All 5 pages render without errors
- [ ] Nav bar present on all pages
- [ ] Approve/Skip buttons update item status via HTMX (no full page reload)
- [ ] Approve All marks all pending items approved
- [ ] Apply page shows live SSE output
- [ ] Settings form saves to DB and pre-populates on next visit
- [ ] htmx.min.js and alpine.min.js loaded from `/static/js/` (no CDN)
- [ ] No raw API keys visible in HTML output
