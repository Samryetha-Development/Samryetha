# Samryetha

Samryetha is the forum application: a React/Vite SSR frontend and a FastAPI API.
Authentication is delegated to Lako over OpenID Connect; Lako is deployed and
versioned independently.

## Repository boundary

This repository owns only the forum:

- `backend/` — FastAPI API, persistence, moderation, feedback, tasks and tests.
- `frontend/` — React SSR client and forum-owned UI primitives.
- `docs/` — forum architecture, API and operations documentation.

Related projects live in separate repositories:

- [Lako](https://github.com/Samryetha-Development/Lako) — identity provider and account UI.
- [Lako UI](https://github.com/Samryetha-Development/lako-ui) — reusable authentication/components package.
- [Samryetha Ops](https://github.com/Samryetha-Development/Samryetha-Ops) — production deployment and operations.

The forum imports a commit-pinned `@lako/ui` Git dependency. It does not import
source from a sibling checkout. Forum translations are bundled locally so SSR and
hydration do not require the translation service at runtime.

## Requirements

- Python 3.12+
- Node.js 20+
- pnpm
- uv

## Local setup

```bash
python bootstrap.py
python bootstrap.py --dev
```

The development services are:

| Service | URL |
| --- | --- |
| Forum frontend | http://localhost:3000 |
| Forum backend | http://localhost:3001 |

`bootstrap.py` does not start Lako. Configure `backend/.env` with a reachable
OIDC issuer, or run the standalone Lako repository separately.

Run services individually:

```bash
cd backend
uv sync
uv run python -m samryetha.main

cd frontend
pnpm install
pnpm dev
```

## Verification

```bash
cd backend
uv run python -m compileall -q src
uv run ruff check .
uv run pytest

cd ../frontend
pnpm typecheck
pnpm build
pnpm test:artifacts
pnpm check:artifacts
node scripts/ssr-smoke.mjs
node scripts/auth-redirect-smoke.mjs
```

## Configuration and production

Copy `backend/.env.example` to `backend/.env`. Never commit secrets. Production
must replace the published default secrets/passwords, use an HTTPS `APP_ORIGIN`
and `COOKIE_SECURE=true`. Local registration requires only username and password;
`ALLOWED_EMAIL_DOMAINS` is a legacy setting, not a registration requirement. Configure
the Lako OIDC issuer/client values if using external identity.

Public-beta verification, recovery behavior, migration and rollback instructions:
[readiness report](backend/docs/public-beta-readiness-20261020.md) and
[operations runbook](backend/docs/public-beta-operations.md).

Production orchestration belongs to
[Samryetha Ops](https://github.com/Samryetha-Development/Samryetha-Ops); this source
repository no longer deploys or supervises Lako or the translation platform.

`pnpm build` checks the installed `@lako/ui` entry points before bundling, then
checks browser module imports, referenced chunks/styles/fonts, and SSR dependency
resolution and rendering. Missing dependency `dist` files or bare browser imports
fail the build even if Vite itself succeeds. CI uses a fresh checkout and isolated
pnpm store with `--frozen-lockfile`; production must run the same artifact gate
before replacing output or restarting workers. Retain the pnpm lockfile instead
of mixing it with `npm ci` (which requires a separate npm lockfile).

Samryetha is licensed under the GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
