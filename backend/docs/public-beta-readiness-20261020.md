# Samryetha public-beta readiness — 2026-10-20

## Decision

**Code is suitable for a small, moderated public beta after the bounded target-environment checks in [the operations runbook](public-beta-operations.md). Production deployment itself is not verified or authorized by this audit.** Existing registration approval stays in place. No framework migration, redesign, production modification, commit or push was performed.

The working checkout is `codex/split-services` at `e871e5f` plus existing uncommitted work. The initial checkout already contained message/realtime, UI, vendor and artifact-check changes; they were preserved. The pre-change backend baseline was 452 passing tests and frontend type checking passed. This report assesses the resulting working tree, not just committed HEAD.

Actual tools: existing backend environment Python 3.12.13; an independent frozen environment Python 3.14.3; Node 22.23.2; pnpm 10.33.2. The repository supports Python >=3.12, rather than requiring 3.14 exclusively.

## Actual feature state

| Area | State after remediation | Evidence / limits |
| --- | --- | --- |
| Username/password registration | Implemented and verified | Server username validation/uniqueness, Argon2id, no email/phone required, pending approval. Browser created `betaregister` in an isolated DB and displayed its one-time key. |
| Password login, logout, sessions | Implemented and verified | Active-account enforcement, hashed opaque session tokens, expiry, cookie attributes, revocation; browser ordinary/admin login, logout and reload. No separate application refresh-token rotation system exists. |
| Local password recovery | Implemented and verified after fixes | Recovery Key registration/setup/replacement, atomic consumption, key rotation, mailbox-token recovery, replay rejection and old-session/QR revocation. Existing accounts are not assigned invented plaintext keys. |
| OIDC and QR | Implemented; external verification incomplete | Existing OIDC/QR/backend regressions and frontend redirect/response smokes pass. No live external IdP, production callback or real email-code delivery was certified. With password auth retired, identity-provider recovery remains provider-owned; no local bypass or newly invented Passkey flow was introduced. |
| Boards, discussions, nested replies | Implemented and verified | Existing access/deletion/attachment/pagination tests; browser publication, nested replies and reload persisted. |
| Follow, notifications, realtime | Implemented and verified | Existing outbox/state snapshot tests; browser recipient received follow notification and messages, and unread count changed after reading. SSE now also honors revocation during an open connection. |
| Search | Implemented and verified | Current visibility predicates and bounded result-page tests; browser returned the actual published discussion and restored search after refresh through existing history state. It was not incorrectly classified as missing because the query was not in the URL. |
| Private messages | Implemented and verified, with scale limits | Participant checks, privacy switch, nonparticipant denial, previews, new history pagination, cross-account/in-flight isolation and actual two-account browser send/read. Conversation list itself remains unpaged; no large-installation capacity claim. |
| Tasks | Implemented and verified | Existing feature is administrator-only. Browser created/completed a disposable task and checked refresh persistence; ordinary-user denial is intentional. |
| Reporting/moderation | Implemented and verified | Discussion/reply/account reports, admin-only queue, removal/ban/restore/audit tests. Browser account report → queue → dismiss → `report.dismiss` audit record. Account reporting is now reachable from profile and conversation UI. |
| Automated content pre-review | Not necessary for this beta | Intentionally retired by the existing product implementation, not a missing module to recreate. Human reports and moderation remain. |
| Per-user blocking/muting | Missing, not required for the initial approved-account beta | No claim that this exists. Existing DM-disable preference and account reporting are accessible; account reports explicitly do not claim to block a sender. Reassess with actual abuse reports. |
| Feedback/help | Implemented and verified in its actual scope | Existing project-member Feedback remains; public GitHub issue tracker verified public/enabled. `/community` provides conduct/privacy/help and explains project access. Operator must publish a private contact before opening. |
| Events/calendar | Not identified as a separate calendar feature | Existing event bus is implemented. A new calendar/product subsystem is not needed for the beta and was not invented. |
| Production backup/restore/rollback | Implementation tested; production drill outstanding | Disposable actual restore and corrupted-candidate protection passed. Production backup, upload pairing, retention and rollback remain operator acceptance items. |

## Concrete findings fixed

Paths below are repository-relative. Each reproduction used isolated data or existing test fixtures.

| ID | Reproduction and original issue | Fix location and verification |
| --- | --- | --- |
| B01 | Register without email, then open `/forgot-password`: only an email-based form existed, leaving no no-email recovery path. | `auth/{service,router,models}.py`, `core/schema.py`, frontend recovery/signup/settings pages. New 256-bit keys are shown only in transient UI, stored as digests, consumed conditionally and rotated. Recovery frontend smoke and `test_beta_readiness.py`. |
| B02 | An admin could call `/api/admin/users/:id/reset-password` and obtain a victim's temporary password without victim-held recovery proof. | `admin/service.py` always returns 410 after authorization; route deprecated/documented; dead reset UI removed. Updated regression asserts the victim password and session remain unchanged. Public route/type retained for compatibility, behavior intentionally retired for the stated security policy. |
| B03 | Existing sessions and already approved QR tickets could outlive a password-recovery operation. Outstanding reset links also survived Change Password. | `auth/service.py` revokes sessions, outstanding reset tokens and approved QR tickets. Recovery/mail replay and QR-exchange regressions. |
| B04 | Restart with an existing `admin`/`dev` name: bootstrap promoted/reactivated it; changing an environment password did not remove an already stored published default. | `AuthService.ensure_builtin_accounts()` never promotes/reactivates/replaces existing credentials, does not create production `dev`, and refuses active built-in accounts retaining published defaults. Both cases have regressions. |
| B05 | Set `NODE_ENV=production` with HTTP origin or insecure cookies: previously only an HTTP warning existed. | `core/config.py` now refuses both configurations. Regression plus `.env.example`/runbook. Public proxy/TLS and trusted-header behavior still require target checks. |
| B06 | Break DB connectivity then request `/api/health`: original error body still returned 200. | `system/health_router.py`: 503 with a generic error body. Regression injects connection failure. |
| B07 | Many unique client keys left expired limiter buckets indefinitely; concurrent calls had no lock. | `core/middleware.py`: synchronized buckets, periodic reclamation, bounded capacity, fail-closed overflow. Tests expire 10,000 buckets and verify forwarded-header spoofing cannot bypass auth limits when proxy trust is off. |
| B08 | Mutations with cross-site Fetch Metadata and large JSON bodies lacked explicit resource/metadata guards. | Middleware rejects cross-site mutations and JSON over 1 MiB, including streamed bodies; existing 50 MiB file-upload stream constraints remain. Auth responses are no-store; API/SSR safety headers added without duplicate `nosniff`. Regressions preserve download MIME assertions. |
| B09 | Disable public profile or online-status preference: public-profile API ignored both. | `users/service.py`: hidden profiles return 404 to others, hidden last-seen returns null. Owner access remains. Existing public content is still public, and aggregate online count does not disclose identities. Regression. |
| B10 | Account deletion left optional recovery email and Recovery Keys behind. | `admin/service.py` clears email/preferences, deletes key and invalidates pending reset links alongside existing anonymization/session revocation. Deleted accounts remain excluded by auth lookups. Existing content/reference retention is disclosed. |
| B11 | Conversation previews loaded every historical message; thread history returned an unbounded list. | `messages/repository.py` selects only each conversation's last message. Thread GET defaults to 50, max 100, with optional `before` and additive `nextCursor`; `(conversation_id,id)` index added. UI loads older history. 55-message paging and nonparticipant tests. |
| B12 | While changing conversation/account, old messages/recipient remained; a late response could repopulate another account's inbox. | `frontend/src/inbox-page.tsx` and `lib/realtime.ts`: clear identity-owned state, cancel old generations, clear recipient on switch, and reconnect identity-bound SSE snapshots; guard late results. Real component regression exercises late private responses after switching account. |
| B13 | After sending the first message from `?to=...`, refresh recreated the compose form. | Inbox removes only `to` after successful send; component regression and actual browser verification (`/inbox` afterwards). |
| B14 | Backend accepted account reports, but profile/inbox had no corresponding user-report control. | Existing `ReportButton` supports `user`; profile and conversation expose it, including a private-profile sender. Explanation states this does not automatically block messages. Real-component regression and browser moderation/audit flow. |
| B15 | Backend SSE checked authentication only when connecting; a revoked stream could continue emitting unread state. | `events/realtime_router.py` revalidates before protected frames and on keepalive; closes and releases leases after logout/ban/expiry. Three deterministic stream-lifecycle regressions. Detection when idle is bounded by the existing 15-second keepalive. |
| B16 | Backup restore copied directly over the DB and swallowed restore errors. | `feedback/backup.py`: staged integrity/schema validation, preservation backup, WAL checkpoint, atomic replacement, explicit startup failure and retained marker. Actual restore + corrupt-candidate regression. All writers must be stopped. |
| B17 | A stolen session could bind an attacker's recovery mailbox without knowing the current password, then reset the password. | `users/{models,router,service}.py`, settings UI/API: changed/removed mailbox requires `currentPassword`, validates address and invalidates prior links. Unchanged mailbox/profile saves retain their prior behavior. Empty string now securely removes optional email. Regression covers missing/wrong/correct proof and removal. |
| B18 | Production npm dependency audit returned four advisories. | Frozen overrides update proxy-addr/qs and Mermaid's KaTeX; direct KaTeX stays on its supported minor. A subsequent full audit also found and fixed build-only source-map-js via 1.2.2. Both production and full npm audits now report zero. Advisory severity does not prove exploitation in this deployment: Express does not enable the problematic trust-subnet setting. See upstream [proxy-addr](https://github.com/jshttp/proxy-addr/security/advisories/GHSA-jqcg-44mw-7w3h), [qs](https://github.com/ljharb/qs/security/advisories/GHSA-4mjr-xmp4-gh2g), [KaTeX](https://github.com/KaTeX/KaTeX/security/advisories/GHSA-238p-pmpm-9mq7). |
| B19 | Default Uvicorn access logging included callback authorization codes and signed-query credentials in request targets. | `core/logging.py` and `main.py`: the shipped runtime redacts query strings from access records while retaining path/method/status. Regression asserts auth-code and signed-URL queries never appear. Configure the external proxy similarly; its live logs were not changed. |

Authorization review covered current dependency guards and service policies for profiles, private messages, tasks, boards/discussions/replies, attachments, files, scheduled posts, search, reports/moderation and Feedback/Agent access. Existing IDOR/visibility regressions were retained. Inactive actors now also fail the shared service authorization policy, not just route guards. This is evidence from code/tests, not a claim of exhaustive penetration testing or legal certification.

## Verification executed

| Check | Result |
| --- | --- |
| Initial backend baseline | 452 passed; one existing Starlette/httpx deprecation warning |
| Python 3.12.13 full suite | 474 passed, no failures/skips; same deprecation warning |
| Frozen separate Python 3.14.3 environment | 474 passed, same warning; did not replace the existing virtualenv |
| Backend strict typing / lint / compile | basedpyright 0 errors/warnings; Ruff passed; compileall passed |
| OpenAPI and frontend generated types | Re-exported and regenerated; typecheck passed. Intentional compatibility changes above documented. |
| Frontend new beta regression | Recovery validation/retry/key display, mail URL cleanup, inbox identity/late-response isolation, compose URL cleanup account-report delivery and identity-bound realtime snapshots passed |
| Existing frontend smokes | Auth redirect, OIDC response, Polls StrictMode, Files StrictMode, write failures with negative controls, drag/drop, folders, metadata, file UI usability, scheduled posts and post pagination passed |
| Artifact regression | Ten checks passed, including deliberate missing module/CSS/font/chunk and traversal cases |
| Production build | Passed; 158 client files, SSR imports resolved; 22 SSR routes/locales passed. Existing large diagram chunks still warn; no performance/load SLA claimed. |
| Independent dependency install/build | Source snapshot in a new temporary directory, new pnpm store, frozen install, typecheck and production/SSR/artifact build passed |
| Dependency audits | npm production and full audits: zero known advisories after fixes; pip-audit on installed backend packages: zero known advisories, editable first-party package explicitly excluded from advisory lookup |
| Git hygiene | Diff whitespace check passed; only tracked environment file is `.env.example`; no tracked PEM/key/credentials file was listed. This is not a complete history secret scan. No commit/push. |

One pre-existing pagination smoke harness omitted the newly used `useRealtimeState` injection and failed with ReferenceError. The missing dependency stub was supplied without weakening list assertions; the smoke then passed. A local OIDC smoke also tried to bind the already used HMR port; it now disables HMR/WebSocket because this is a server-side module smoke. Two failures introduced during this work came from duplicate safety headers; the middleware was fixed and the original download assertions remain intact. No failing tests were hidden or skipped.

## Browser evidence and workflow coverage

Tested built SSR frontend at `http://localhost:3910`, isolated Python API `:3911`, disposable SQLite/uploads under `backend/.pytmp-beta/`. No production data or existing service was used. Browser tests used dummy accounts and no real personal data.

- Registered without email, saw key/approval guidance; logged in as ordinary user; logout hid inbox; another account received the actual messages/follow notification.
- Created discussion, reply and nested reply; refreshed and saw both replies persist. Existing empty state was tested before seeding the single board.
- Searched published content; refresh restored the existing search state. Missing discussion showed a clear unavailable-resource message. Ordinary account was denied `/admin`.
- Sent first message and checked URL cleanup; reported sender; administrator saw report context, dismissed it and read the resulting `report.dismiss` audit entry.
- Administrator created/completed a task; refresh retained the completed state. This is the actual product permission model, not an invented ordinary-user task feature.
- Tested CSS viewports 390×844, 767×900, 769×900 and 1280×900, scale 1; no document horizontal overflow on the inspected recovery layouts. Recovery input frames had 1px borders; narrow form width was 301px. Light/dark media switching worked. Temporary viewport/media overrides were reset.
- Updated KaTeX and Mermaid were also rendered in a real Markdown discussion: two KaTeX instances (including a math diagram label), one diagram SVG, no browser errors.
- Actual recovery protocol, password mutation, duplicate consumption and failure states are verified through API/component regressions; live production password changes, external SMTP and IdP recovery were not performed.

Local screenshots: `frontend/.dsh-verify/beta/recovery-390-{light,dark}.png`, `recovery-1280-light.png`, `moderation-390.png`, `math-diagram-1280-dark.png`. Logs: `backend/.pytmp-beta/{final-pytest,python314-tests,clean-install,clean-build}.log` and `frontend/.dsh-verify/beta/build.log`. These local artifacts/databases are intentionally ignored rather than shipped. The temporary browser tab was closed, viewport/media overrides reset, and ports 3910/3911 were confirmed no longer listening at cleanup. Existing application services were not stopped.

## Remaining acceptance and follow-up

Before exposing the real site, perform the three concrete runbook checks: isolated restoration of the real backup set, actual domain/TLS/auth/delivery checks, and moderator/contact/backup ownership. These are the remaining release gates. No production data migration, live TLS/OIDC/SMTP probe, multi-process realtime verification, power-loss recovery or production rollback drill was performed.

Nonblocking follow-up for the initially moderated beta: paginate the conversation list for much larger accounts; measure notification/message/query load on real traffic; introduce per-user posting/messaging quotas or blocking if current approval/report/ban/privacy controls prove insufficient. Existing global/IP limits are not a complete defense against distributed abuse. Do not claim general capacity, zero vulnerabilities or legal compliance from these results.
