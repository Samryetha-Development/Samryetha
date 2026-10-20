# Lako UI package snapshot

`lako-ui-e47f861.tgz` packages the committed source at
`Samryetha-Development/lako-ui` commit
`e47f86179322472f2a11f806ee5fe9fcff4e978d`.
That commit adds `LakoTimeDialog` and is not yet on the remote main branch.
The snapshot excludes the source checkout's uncommitted changes.

To reproduce, export that commit into an isolated directory, run
`pnpm install --frozen-lockfile`, then `pnpm build` and
`pnpm pack --out <path>/lako-ui-e47f861.tgz`.
The forum installs this portable package without sibling-source dependencies.
