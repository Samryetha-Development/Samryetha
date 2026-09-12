# Lako Auth

Lako is a lightweight, self-hosted identity and authentication platform. It is a modular monolith: one FastAPI identity provider, one PostgreSQL database, and a small Next.js account UI. The included Samryetha client performs a real OAuth 2.0 Authorization Code + PKCE flow.

## Run the complete flow

Prerequisites: Docker Compose, or Python 3.12+, `uv`, Node 20+, and pnpm.

```bash
git clone <repository>
cd Samryetha/lako
cp .env.example .env
docker compose up --build
```

Then:

1. Open `http://localhost:3000/register` and create an account.
2. Open `http://localhost:4000` and select **Sign in with Lako**.
3. Sign in at Lako. The client callback exchanges the one-time code and shows the name, stable user ID, and email returned by `/oauth/userinfo`.

Compose runs `alembic upgrade head` and `python -m app.cli seed` before starting the API. The seed is idempotent and registers public PKCE client `samryetha` with `http://localhost:4000/auth/callback` and the requested `http://localhost:3000/auth/callback` development URI.

## Local development

```bash
cd lako/api
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python -m app.cli seed
uv run uvicorn app.main:app --reload --port 8000

cd ../web && pnpm install && pnpm dev
cd ../examples/samryetha-client && pnpm install && pnpm dev
```

Run checks with `make test` and `make build`, or run each command directly on Windows.

Create an administrator without shipping a default password:

```bash
cd lako/api
uv run python -m app.cli create-admin --username admin --email admin@localhost --password "replace-with-a-long-password"
```

## Architecture and data

The API is organized by domain under `app/`: `identity`, `authentication`, `sessions`, `oauth`, `authorization`, `security`, and `common`. User is the person; Identity stores normalized username/email identifiers; Credential stores Argon2id password material; Device describes a non-invasive browser association; Session stores server-side login state; AuthorizationCode and AccessToken belong to OAuth authorization. Role/Permission joins implement namespace-based RBAC. AuditEvent is append-only by application convention.

All IDs are UUIDs and times are UTC. Indexed lookup columns include normalized identities, hashed sessions/codes/access tokens, users/devices, and OAuth client IDs. The initial Alembic migration is the only schema initialization path used by Compose.

## Endpoints

- `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- `POST /api/auth/login/mfa`
- `GET /api/account/mfa`, TOTP setup/confirm/disable, step-up, and recovery-code regeneration endpoints
- `GET /api/sessions`, `POST /api/sessions/logout`, `POST /api/sessions/logout-others`
- `GET /api/devices`, `POST /api/devices/{id}/revoke`
- `GET /.well-known/openid-configuration`, `GET /.well-known/jwks.json`
- `GET /oauth/authorize`, `POST /oauth/token`, `GET /oauth/userinfo`

Normal API errors use `{"error":{"code":"...","message":"..."}}`; OAuth protocol endpoints use OAuth error fields.

## Security decisions

- Passwords use Argon2id. Random session tokens, authorization codes, and access tokens come from Python `secrets`; only SHA-256 lookup hashes are persisted.
- Sessions are server-side, revocable, device-bound, HttpOnly, SameSite=Lax cookies. Mutations use a double-submit CSRF token. Production must set `COOKIE_SECURE=true`.
- Redirect URIs use exact database equality. Only Authorization Code + mandatory PKCE S256 is accepted. Codes are short-lived, client/redirect/user/challenge/nonce-bound and single-use.
- ID tokens are short-lived RS256 JWTs with minimal `iss`, `aud`, `sub`, `iat`, `exp`, and `nonce` claims. Access tokens are opaque and expire in 15 minutes. A stable private key must be supplied in production; ephemeral generation is development-only.
- CORS origins are explicit. Return paths accepted by the UI are constrained to the Lako authorization route. Audit metadata never includes submitted passwords, tokens, codes, or verifiers.

To add the production Samryetha callback, insert an `OAuthRedirectURI` with the exact URI `https://samryetha.com/auth/callback`; never use prefixes or wildcards. Samryetha should generate and retain state, nonce, and PKCE verifier in HttpOnly short-lived transaction cookies, validate state with a constant-time comparison, exchange the code server-side, validate the ID token against discovery/JWKS, and establish its own application session.

## Configuration

Security-sensitive settings are centralized in `app/common/config.py`. Copy `api/.env.example`; never commit `.env` or private keys. In production use HTTPS origins, `COOKIE_SECURE=true`, a persistent RSA key (or managed key path integration), and a non-default PostgreSQL password.

## Multi-factor authentication (M2)

M2 is implemented: users can enroll an authenticator by QR code, confirm it before activation, receive ten one-time recovery codes, complete TOTP or recovery-code login, and step an AAL1 session up to AAL2. TOTP secrets are encrypted at rest; recovery codes and login challenges are stored only as hashes. Challenges expire after five minutes, are single-use, and lock after five failed attempts. Security-setting changes require AAL2 verified within the last ten minutes.

Later phases can add WebAuthn/passkeys, refresh-token families and security notifications, external IdPs, then organization-aware authorization.
