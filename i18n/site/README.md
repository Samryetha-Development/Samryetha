# Samryetha Translation Site

Standalone Vite + React single-page app for community translation submissions.
Talks to the main Samryetha backend (`/api/i18n/*`) for all data.

## Pages

| Tab | Auth required | Description |
|-----|--------------|-------------|
| Source Strings | No | Browse the full source-string catalog; view approved translations per language |
| Submit Translation | Yes (active user) | Submit a translation for any source string |
| My Submissions | Yes | Track the status of your own submissions |
| Admin Review | Yes (admin / moderator) | Approve or reject pending submissions |

## Development

```bash
cd i18n/site
npm install
npm run dev          # starts on :5200, proxies /api → http://localhost:3001
```

To point to a different backend:

```bash
VITE_API_TARGET=http://my-backend:3001 npm run dev
```

Type-check:

```bash
npm run typecheck
```

## Production build

```bash
npm run build        # output in i18n/site/dist/
```

The `dist/` directory contains a standard SPA (`index.html` + assets).
All routes fall through to `index.html`, so the server must serve it for
unknown paths.

### Serving from the FastAPI backend

Add the following to `backend/src/samryetha/main.py` (inside `create_app`):

```python
from fastapi.staticfiles import StaticFiles
import pathlib

dist = pathlib.Path(__file__).parent.parent.parent.parent / "i18n" / "site" / "dist"
if dist.is_dir():
    app.mount("/i18n", StaticFiles(directory=str(dist), html=True), name="i18n-site")
```

Then build the site and the backend will serve it at `/i18n/`.

### Serving from Nginx

```nginx
location /i18n/ {
    alias /srv/samryetha/i18n/site/dist/;
    try_files $uri $uri/ /i18n/index.html;
}
```

Build the site, copy `dist/` to `/srv/samryetha/i18n/site/dist/`, then reload nginx.

### Separate subdomain / port

Run any static file server pointing at `dist/`:

```bash
npx serve dist -p 5200
# or
python3 -m http.server 5200 --directory dist
```

Configure the backend CORS `allow_origins` to include the subdomain, and set
`VITE_API_BASE=https://samryetha.example.com` before building so API calls go
to the right host.

## API endpoints (backend)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/i18n/catalog` | Public | All source strings; `?lang=zh-Hans` attaches approved translations |
| `GET` | `/api/i18n/catalog/{key}` | Public | Single source string |
| `GET` | `/api/i18n/submissions` | User / Admin | List submissions (admin sees all) |
| `POST` | `/api/i18n/submissions` | Active user | Submit a translation |
| `GET` | `/api/i18n/submissions/{id}` | User | Single submission |
| `POST` | `/api/i18n/submissions/{id}/approve` | Admin / Mod | Approve |
| `POST` | `/api/i18n/submissions/{id}/reject` | Admin / Mod | Reject with optional reason |

## Design

Follows `frontend/design.md` — near-white background, `--line` 1 px borders,
muted-blue accent (`--accent-fill: #3d7dbf`), pill-radius buttons, system font stack.
No external UI library; all styles in `src/styles.css`.
