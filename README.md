# Samryetha

Samryetha is a full-stack campus forum built for the Nanjing Foreign Language School community. It provides discussion boards, threaded replies, user profiles, follows, notifications, search, attachments, presence, moderation, and role-based administration.

The project is a modular monolith with a React SSR frontend and a Fastify API backed by SQLite. Development infrastructure is intentionally local-first, while typed interfaces leave room for production services such as PostgreSQL, Redis, S3, and SMTP.

## Features

- Email-domain-restricted registration and cookie-based sessions
- Public, member-only, and policy-controlled discussion boards
- Markdown posts, threaded replies, saves, follows, and user profiles
- Real-time notifications over server-sent events (SSE)
- Search, online presence, and signed attachment uploads
- Moderator reports, bans, content restoration, and audit history
- OpenAPI documentation and an authorization capability matrix

## Tech Stack

- **Frontend:** React 19, Vite, TypeScript, Tailwind CSS, Express SSR
- **Backend:** Fastify 5, Zod, Drizzle ORM, SQLite, Argon2id
- **Testing:** Vitest with unit tests and Fastify injection-based integration tests
- **Package manager:** pnpm

## Quick Start

### Prerequisites

- Node.js 22 recommended (`node:sqlite` is used by the backend)
- pnpm
- Python 3

Clone the repository, then run:

```bash
python bootstrap.py --dev
```

The bootstrap script installs both packages, copies `backend/.env.example` to `backend/.env`, applies migrations, ensures the built-in `admin` / `dev` accounts exist, and starts both servers.

| Service | URL |
| --- | --- |
| Web application | <http://localhost:3000> |
| API | <http://localhost:3001> |
| Swagger UI | <http://localhost:3001/docs> |

Stop both development servers with `Ctrl+C`.

### Other Bootstrap Options

```bash
python bootstrap.py                 # Set up without starting servers
python bootstrap.py --skip-install  # Reuse installed dependencies
python bootstrap.py --skip-db       # Skip migrations and built-in accounts
```

## Manual Development

Create the backend environment file and install each package:

```bash
cp backend/.env.example backend/.env
cd backend && pnpm install
cd ../frontend && pnpm install
```

Run the services in separate terminals:

```bash
cd backend && pnpm dev
cd frontend && pnpm dev
```

The frontend proxies `/api` requests to the backend. Database migrations run automatically when the backend starts; the built-in `admin` / `dev` accounts are created on startup (idempotently).

## Production Deployment

On an Ubuntu server with `node >= 20`, `pnpm`, `pm2`, and `nginx` installed:

```bash
./deploy.sh                                # Deploy on the server IP (http)
DOMAIN=forum.example.com ./deploy.sh       # Deploy behind a domain (http)
DOMAIN=forum.example.com SSL=1 ./deploy.sh # Deploy with Let's Encrypt HTTPS
```

The script installs dependencies, builds both packages, generates `backend/.env` (kept untouched if it already exists), starts `samryetha-backend` / `samryetha-frontend` under pm2, configures an nginx reverse proxy, optionally provisions SSL, and runs health checks. See [`backend/docs/architecture.md`](backend/docs/architecture.md) for variable reference and security notes.

## Common Commands

Run commands from the relevant package directory.

| Package | Command | Purpose |
| --- | --- | --- |
| Backend | `pnpm test` | Run the Vitest suite once |
| Backend | `pnpm test:watch` | Run tests in watch mode |
| Backend | `pnpm seed` | Ensure built-in `admin` / `dev` accounts exist |
| Backend | `pnpm build` | Compile TypeScript into `dist/` |
| Frontend | `pnpm typecheck` | Validate TypeScript types |
| Frontend | `pnpm build` | Build browser and SSR bundles |
| Either | `pnpm start` | Start a production build |

## Project Structure

```text
.
├── backend/
│   ├── docs/          # Architecture, API, schema, and authorization references
│   ├── drizzle/       # SQL migrations and metadata
│   ├── src/           # Feature modules and infrastructure adapters
│   └── tests/         # Unit and integration tests
├── frontend/
│   └── src/           # React pages, components, and client utilities
├── bootstrap.py       # Cross-platform development setup
└── AGENTS.md          # Contributor guidelines
```

See [`backend/docs/architecture.md`](backend/docs/architecture.md) for module boundaries and data flow, and [`backend/docs/api-contract.md`](backend/docs/api-contract.md) for endpoint behavior.

## Configuration

Development settings are documented in `backend/.env.example`. Before deploying, set a strong `STORAGE_SECRET`, configure `ALLOWED_EMAIL_DOMAINS`, set the correct `APP_ORIGIN`, and enable `COOKIE_SECURE` behind HTTPS. Optional Authentik OIDC setup is documented in [`backend/docs/oidc.md`](backend/docs/oidc.md). Never commit `.env`, database, or upload files.

## Testing

```bash
cd backend
pnpm test
```

Integration tests use an in-memory SQLite database. Frontend changes should also pass `pnpm typecheck` and `pnpm build`.

## Contributing

Read [`AGENTS.md`](AGENTS.md) before contributing. Pull requests should describe the change, list verification performed, identify migrations or configuration changes, and include screenshots for visible UI updates.

## License

Samryetha is distributed under the [GNU Affero General Public License v3.0](LICENSE).
