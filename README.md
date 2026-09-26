# Samryetha

Samryetha is a full-stack campus forum built for the Nanjing Foreign Language School community. It provides discussion boards, threaded replies, user profiles, follows, notifications, search, attachments, presence, moderation, and role-based administration.

The forum is a React SSR frontend plus a FastAPI API backed by SQLite. Authentication is delegated to the self-hosted Lako identity provider. Forum and Tasks translations are bundled from the repository's English and Simplified Chinese dictionaries. Development infrastructure is intentionally local-first, while typed interfaces leave room for production services such as PostgreSQL, Redis, S3, and SMTP.

## Features

- Email-domain-restricted registration and cookie-based sessions
- Public, member-only, and policy-controlled discussion boards
- Markdown posts, threaded replies, saves, follows, and user profiles
- Real-time notifications over server-sent events (SSE)
- Search, online presence, and signed attachment uploads
- Moderator reports, bans, content restoration, and audit history
- OpenAPI documentation and an authorization capability matrix

## Tech Stack

- **Forum frontend:** React 19, Vite, TypeScript, Tailwind CSS, Express SSR
- **Forum backend:** Python 3.12, FastAPI, SQLAlchemy Core, SQLite
- **Tasks frontend:** standalone React/Vite application using the forum Tasks API
- **Identity:** Lako — FastAPI OIDC provider with a Next.js authorization UI
- **Languages:** English and Simplified Chinese, shared by the forum and Tasks site
- **Testing:** pytest for the Python services
- **Package managers:** uv (Python), pnpm / npm (Node)

## Quick Start

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and pnpm

Clone the repository, then run:

```bash
python bootstrap.py --dev
```

The unified bootstrap installs each package, generates service `.env` files, migrates and seeds the databases, and starts the forum, Tasks, and Lako processes in one process tree. Stop everything with `Ctrl+C`.

| Service | URL |
| --- | --- |
| Forum web application | <http://localhost:3000> |
| Forum API | <http://localhost:3001> |
| Tasks application | <http://localhost:5300> |
| Lako authorization UI | <http://localhost:4010> |
| Lako API | <http://localhost:8000> |

The forum signs in through Lako over OIDC.

### Bootstrap Options

```bash
python bootstrap.py                           # Set up without starting servers
python bootstrap.py --dev --skip-install      # Reuse installed dependencies
python bootstrap.py --dev --only forum        # Lako + forum only
python bootstrap.py --dev --only lako         # Lako only
```

Each service also has a standalone launcher that pulls up Lako automatically:

| Script | Starts |
| --- | --- |
| `LakoBootstrap.py` | Lako API + authorization UI |
| `ForumBootstrap.py` | Lako + forum backend + forum frontend + Tasks frontend |

All scripts reuse an already-running Lako and shut down their children together on `Ctrl+C`.

## Manual Development

Each service reads its own `.env` (copied from `.env.example`) and can run on its own. Start Lako first for forum sign-in.

Lako (identity provider):

```bash
cd lako/api && cp .env.example .env && uv sync
uv run alembic upgrade head && uv run python -m app.cli seed
uv run uvicorn app.main:app --reload --port 8000

# second terminal
cd lako && pnpm install && pnpm --dir packages/ui build
cd web && LAKO_API_INTERNAL_URL=http://localhost:8000 pnpm exec next dev -p 4010
```

Forum:

```bash
cd backend && cp .env.example .env && uv sync
uv run python -m samryetha.main

# second terminal
cd frontend && pnpm install && pnpm run build:ui && pnpm dev

# third terminal — independent Tasks application
cd tasks/site && pnpm install && pnpm dev
```

The forum and Tasks frontends proxy `/api` requests to the forum backend. Database schemas and seeds are applied idempotently on startup.

## Production Deployment

On an Ubuntu server with `python3`, `uv`, `node >= 20`, `pnpm`, `pm2`, and `nginx` installed:

```bash
./deploy.sh                                # Deploy on the server IP (http)
DOMAIN=forum.example.com ./deploy.sh       # Deploy behind a domain (http)
DOMAIN=forum.example.com SSL=1 ./deploy.sh # Deploy with Let's Encrypt HTTPS
```

With a real domain and HTTPS, Lako is provisioned on `auth.<domain>`; it is skipped for IP-only deployments. The script installs dependencies, builds the packages, generates each `.env` (kept untouched if it already exists), runs migrations and seeds, starts `samryetha-backend`, `samryetha-frontend`, `lako-api`, and `lako-web` under pm2, configures nginx, optionally provisions SSL, and runs health checks. Existing i18n data and configuration files are left in place for recovery, while its pm2 process and generated nginx link are disabled. See [`backend/docs/architecture.md`](backend/docs/architecture.md) for variable reference and security notes.

## Common Commands

Run these from the repository root.

| Package | Command | Purpose |
| --- | --- | --- |
| Backend | `cd backend && uv run pytest` | Run the forum API test suite |
| Backend | `cd backend && uv run python -m samryetha.main` | Start the forum API |
| Frontend | `cd frontend && pnpm typecheck` | Validate TypeScript types |
| Frontend | `cd frontend && pnpm build` | Build browser and SSR bundles |
| Tasks site | `cd tasks/site && pnpm build` | Type-check and build the independent Tasks frontend |
| Lako API | `cd lako/api && uv run pytest` | Run the identity provider tests |
| Lako web | `cd lako/web && pnpm build` | Build the authorization UI |

## Project Structure

```text
.
├── backend/           # FastAPI forum API, docs, tests
│   ├── docs/          # Architecture, API, schema, and authorization references
│   ├── src/           # Feature modules and infrastructure adapters
│   └── tests/         # pytest suite
├── frontend/          # React SSR forum client
├── tasks/site/        # Independent React/Vite Tasks application
├── lako/              # Lako identity provider (api/, web/, packages/ui)
├── packages/          # Shared UI packages (ui-commons)
├── bootstrap.py       # Unified launcher for all services
├── LakoBootstrap.py   # Lako only (also the shared launcher library)
├── ForumBootstrap.py  # Lako + forum
└── AGENTS.md          # Contributor guidelines
```

See [`backend/docs/architecture.md`](backend/docs/architecture.md) for module boundaries and data flow, and [`backend/docs/api-contract.md`](backend/docs/api-contract.md) for endpoint behavior.

## Configuration

Development settings are documented in `backend/.env.example`. Before deploying, set a strong `STORAGE_SECRET`, configure `ALLOWED_EMAIL_DOMAINS`, set the correct `APP_ORIGIN`, and enable `COOKIE_SECURE` behind HTTPS. The forum authenticates through the local Lako provider; see [`backend/docs/oidc.md`](backend/docs/oidc.md) for the OIDC reference. Never commit `.env`, database, or upload files.

## Testing

```bash
cd backend && uv run pytest
cd lako/api && uv run pytest
```

Integration tests use an in-memory SQLite database. Frontend changes should also pass `pnpm typecheck` and `pnpm build`.

## Contributing

Read [`AGENTS.md`](AGENTS.md) before contributing. Pull requests should describe the change, list verification performed, identify migrations or configuration changes, and include screenshots for visible UI updates.

## License

Samryetha is distributed under the [GNU Affero General Public License v3.0](LICENSE).
