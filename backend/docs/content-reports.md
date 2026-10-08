# Content reports and review

Every discussion and comment has a Report control in its detail page and in the corresponding feed/profile list. Signed-out visitors are prompted to sign in. Active accounts provide a reason (1–2000 characters after trimming) and submit the report.

## Reporter visibility

`POST /api/moderation/reports` accepts `reportableType` (`discussion`, `reply`, or the existing `user` type), `reportableId`, and `reason`. Discussion/comment reports require an existing, live target in a board the reporter can currently read. Missing or inaccessible targets return 404; blank reasons return 400. Retrying an existing report from the same account returns the same report rather than adding another row.

A discussion/comment report doubles as a durable, account-specific hidden-content record. All report statuses retain this hiding, including `dismissed` and `resolved`. Other accounts and signed-out visitors retain their normal access. There is no automatic global deletion on reporting.

Reported discussions are excluded before pagination from latest, followed, board, author, saved and search results. Their detail, comments and attached downloads return 404 for the reporter, including downloads using previously issued signatures. Reported comments are excluded from the comment tree and author comment feed; their surviving children can be displayed as root comments. Content notifications, unread counts and future notification delivery apply the same hiding rules. No attachment bytes are removed by an ordinary report.

The existing `reports` table stores this state. `reports_reporter_target_idx` indexes `(reporter_user_id, reportable_type, reportable_id)` and is installed idempotently on existing databases by the normal schema-drift startup path. No configuration changes or destructive migrations are needed. Historical discussion/comment reports also hide those targets from their reporters after upgrade.

## Administrator review

`GET /api/moderation/reports?pendingOnly=true` includes both `open` and `in_progress` reports, ordered by descending ID and using the existing `cursor` / `limit` pagination. When supplied, `pendingOnly=true` takes precedence over `status`. The administrator panel exposes pagination, the reason and reporter, current raw content, content author, deletion state and navigation links. Raw content is displayed as text so reported HTML cannot execute.

`POST /api/moderation/reports/{id}/review` requires an active administrator and accepts:

- `action: "delete"`: soft-delete the reported discussion/comment through the existing discussion service and resolve all pending reports for that target. Discussion attachments follow existing deletion/restoration/cleanup behavior; comment counts are decremented only once.
- `action: "dismiss"`: ignore the report by setting `dismissed`; no content or account change.
- `action: "ban"`: permanently ban the server-selected content author (or the target of a user report), revoke their sessions and resolve this report. Existing safeguards prohibit banning yourself or another administrator. Content deletion is a separate decision.

`reason` is optional (maximum 1000 characters); omitted reasons use the original report reason. Review status, content/account writes, audit records and outbox events share one request transaction. Failed actions roll everything back; concurrent or repeated reviews of a terminal report return 409. Missing reports return 404. Review writes revalidate the administrator after acquiring the write lock. Audit events are `report.delete`, `report.dismiss`, or `report.ban`; bans also retain the existing `user.ban` audit/event.

The existing PATCH status-management endpoint remains available for compatibility. Content targets in report responses now additionally include `bodyMarkdown`, `author`, and `isDeleted`. OpenAPI and generated client types describe these fields and the new review endpoint.
