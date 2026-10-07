# Repository Guidelines

## Project Structure & Module Organization

Samryetha contains the forum only. `backend/src/samryetha/` contains the FastAPI API and `backend/tests/` contains pytest coverage. Database schema code lives in `backend/src/samryetha/schema.py`. API and architecture references are in `backend/docs/`; keep them aligned with behavior changes.

The feedback feature (projects/members/items + Agent API + backups) lives in `backend/src/samryetha/feedback/`; its admin UI is the "Feedback" section of `frontend/src/admin-page.tsx` and the user-facing page is `frontend/src/feedback-page.tsx` (route `/feedback`).

The React/Vite SSR client is in `frontend/src/`; shared browser utilities belong in `frontend/src/lib/` and forum-owned UI primitives in `frontend/src/ui-commons/`. Lako, `@lako/ui`, and the translation platform are independently versioned repositories; do not reintroduce sibling-source or `file:../...` dependencies. Generated output, local databases, uploads, and `.env` files must remain untracked.

## Build, Test, and Development Commands

Requires Node.js 20+, pnpm, and uv (Python 3.12+).

- `python bootstrap.py` installs both packages, creates `backend/.env`, migrates the database, and seeds development data.
- `python bootstrap.py --dev` performs setup and starts the forum's two servers. It does not start Lako.
- `cd backend && uv run python -m samryetha.main` runs the FastAPI API on `backend/.env` (default port 3001); `uv run pytest` runs the test suite once. The Python backend runs from source — there is no build step.
- `cd frontend && pnpm dev` runs the SSR development server.
- `cd frontend && pnpm typecheck` checks client types; `pnpm build` creates client and server bundles.

## Coding Style & Naming Conventions

Use strict TypeScript, ES modules, two-space indentation, semicolons, and double quotes. Use `camelCase` for variables/functions, `PascalCase` for React components, types, and enum member names, and kebab-case filenames such as `thread-page.tsx`. Enum serialized values follow their storage or wire contract and are not renamed solely for code style. Keep backend routes in `routes.ts` and business logic in `service.ts`. No formatter or linter is configured, so preserve nearby style and run both TypeScript checks before submitting.

## Testing Guidelines

Backend tests use pytest and live under `backend/tests/`, using temporary SQLite databases where possible. Run `uv run pytest` from `backend/`; there is no formal coverage threshold or frontend test suite. Client changes must pass `pnpm typecheck` and `pnpm build`.

## Commit & Pull Request Guidelines

History is currently limited to initial and merge commits, so no project-specific convention is established. Write short, imperative, scoped subjects (for example, `Add board membership validation`). Pull requests should explain the change, testing performed, configuration or migration impacts, and linked issues. Include screenshots for visible UI changes and update `backend/docs/` for API, schema, authorization, or event changes.

## Security & Configuration

Copy `backend/.env.example` for local use; never commit secrets. Replace `STORAGE_SECRET`, validate `ALLOWED_EMAIL_DOMAINS`, and enable secure cookies before production deployment.
