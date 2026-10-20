# Public-beta operations

This is a release procedure for the existing SQLite/FastAPI + React/Vite SSR application. It does not authorize any production change. Production orchestration remains in Samryetha-Ops.

## Before opening the beta

Use a small, moderated beta with the existing administrator approval of registrations. Do not require email, school email or phone numbers. Assign a named moderator and publish a private site-operator contact in the beta announcement. `/community` explains conduct, reporting, privacy and the existing public project issue tracker; public issues must not contain private messages, credentials or exploit details.

Complete these concrete deployment checks:

1. Restore a real backup **into an isolated clone**, including uploads. Check `PRAGMA integrity_check`, account counts, restricted content and a sample attachment. The automated restoration regression proves the implementation on a disposable database; it is not a production restoration drill.
2. Check the actual HTTPS domain, proxy, cookie attributes, login/logout, registration approval and Recovery Key flow. If OIDC is enabled, check redirects, state/PKCE, callback and logout against that provider. If offering email recovery, verify actual SMTP delivery and mailbox recovery. These external flows were not tested against production by this audit.
3. Confirm an active administrator and moderator coverage, private operator contact, disk space and backup retention. Confirm that development accounts and seeded demonstration content are absent or deliberately disabled. Startup now refuses active built-in accounts still using published default passwords and never reactivates/promotes existing names.

These are a bounded deployment acceptance exercise, not a requirement for new infrastructure or a complete redesign.

## Configuration and release artifacts

- Run `NODE_ENV=production`, an HTTPS `APP_ORIGIN`, `COOKIE_SECURE=true`, independent strong `STORAGE_SECRET` and overridden published built-in passwords. Keep `.env` outside source control. The public development defaults are not deployment credentials.
- Bind the API and SSR listeners to loopback behind the intended TLS proxy. Overwrite incoming forwarded headers at that proxy; only enable `TRUST_PROXY` when every API request passes through a controlled boundary. Do not expose a second path that lets clients supply arbitrary forwarded addresses. Keep CORS to the configured forum origin.
- Terminate HTTPS and set HSTS at the public reverse proxy after validating that all intended hosts support HTTPS. Test the actual host allowlist there. Application SSR responses now set `nosniff`, same-origin referrer policy and CSP `frame-ancestors 'self'; base-uri 'self'; object-src 'none'`. This is a limited policy, not a claim that a full script CSP is enforced.
- Cookies are HttpOnly, SameSite=Lax, with the configured absolute lifetime (default seven days). There is no separate application refresh-token subsystem; OIDC-provider sessions are controlled by that provider. Avoid cookie Domain unless cross-subdomain sharing is specifically needed.
- The shipped live event bus and presence leases are process-local. Use **one API process** for this beta unless a separately verified shared-event deployment is supplied. SQLite is the implemented database; changing an environment value to a PostgreSQL URL does not implement another dialect.
- Keep the existing lockfiles and the checked-in `frontend/vendor` package. Build in a **new release directory**, not over the currently served `dist`. Run `uv sync --frozen --dev`, backend tests/checks, `pnpm install --frozen-lockfile`, `pnpm typecheck`, `pnpm test:artifacts`, `pnpm test:beta`, `pnpm build`, and `pnpm check:artifacts`. Keep the previous release untouched if any command fails.
- Promote the completed release with the existing operator-controlled release mechanism; restart the correct SSR/API processes. Do not assume HMR or an old SSR module cache will load a new production build.

## Schema upgrade

Take a coherent backup before startup. The existing migration mechanism is `Database.create_schema()` plus `ensure_schema_drift()`, called at startup and idempotent. This change adds `recovery_keys` (one hashed key per account) and the conversation/message ID cursor index. It does not fabricate plaintext keys for existing accounts, remove users, or rewrite existing passwords. Existing users generate a key in Settings after verifying their current password.

The application also has the **pre-existing** `retire_content_review()` data conversion, guarded by `content_review_removed_v1`. That conversion is not reversed by old application code. Inspect a clone of the real schema and migration markers before applying an upgrade; do not treat every startup migration as additive merely because this change adds a table.

## Backup and restoration

Backups exposed through the existing admin backup interface are full SQLite snapshots (`VACUUM INTO`), even though the interface lives under Feedback. They do **not** include upload bytes, environment secrets, proxy configuration or application releases. Retain those separately with access restrictions and a documented schedule/retention policy. Backups can contain private messages and historical account information.

For an actual restore:

1. Put the site into maintenance and stop **all** API workers, background workers, and other database writers. Keep a copy of the current release, database, uploads and relevant configuration. Do not copy just a live SQLite main file while discarding its WAL.
2. Select a known full database backup. The existing admin restore action writes `.restore_pending`; it is applied before the application opens the database on the next startup. Test the same operation on the isolated clone first.
3. Restore validation copies the candidate to a staging file, checks SQLite integrity and the users table, preserves the current database as `.pre-restore.sqlite`, checkpoints the old WAL and atomically replaces the database file. A corrupt/missing candidate fails startup visibly and preserves the marker; do not delete a failed restore marker to pretend recovery succeeded. Inspect the error and repair the candidate or deliberately cancel the restore after preserving evidence.
4. Restore matching uploads if needed, start one API process, check `/api/health`, private/public access, login and sample downloads, then reopen traffic. A database restored to an earlier snapshot loses newer activity and credentials; explicitly account for that data-loss window.

The regression includes an actual disposable backup → mutation → restore → integrity/read check, plus a corrupt candidate that must leave the original data available. Power loss, disk failure, multiple simultaneous writers and the production backup set remain unverified.

## Rollback and incidents

Record the prior release and backup identifiers before promotion. For an application-only fault, stop writes and switch to the previous **known-safe** compatible release, then rerun health/authentication/content checks. Do not roll back to a version with the retired administrator password-reset takeover path just to obtain a green health probe.

For a schema/data incompatibility, stop writers and restore the paired database/uploads backup as a separate operation. Rolling back Git does not reverse migrations, password changes, account deletion, moderation actions or event deliveries. Never delete a new recovery table as a shortcut for a rollback. Communicate any restoration data-loss window.

For an account incident, suspend the affected account through authorized moderation, revoke sessions and investigate the audit trail. Recovery requires the account holder's Recovery Key or mailbox proof; an administrator cannot generate a replacement password from a username or ownership assertion. If all recovery methods are lost, explain that account access cannot be restored by bypassing those proofs.

Monitor HTTP 5xx/429 rates, disk space, database lock errors, pending/failed outbox work, scheduled-post failures and actual backup timestamps. `/api/health` is a database-dependent **readiness** probe and returns 503 on failure; it is not proof that SMTP, OIDC or every background job is healthy. Use the process supervisor for liveness. The shipped `python -m samryetha.main` access logger redacts query strings (including authorization codes and signed URLs); configure proxy/access/error-reporting logs to redact query strings and credentials too. Capture request IDs and errors without submitting passwords, recovery keys, reset URLs or session cookies to logs/bug reports.

The current auth limit is ten attempts per minute per client address; the general limit is 300 requests per minute per address, per process. Buckets are bounded and expire. Shared school networks can hit address-based limits; watch real 429 rates and tune the controlled ingress proportionately instead of introducing mandatory identity collection. Registration approval, reports, bans and the direct-message privacy switch are the existing beta abuse controls. More granular per-user quotas/blocking and conversation-list pagination are follow-up work if observed traffic warrants them.
