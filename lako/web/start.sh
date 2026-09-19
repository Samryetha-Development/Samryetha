#!/usr/bin/env bash
# pm2/生产入口：跑 Lako web（Next）。需先构建 @lako/ui 再 `next build`。
# LAKO_API_INTERNAL_URL 由 deploy.sh/pm2 注入，rewrite 目标靠它。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec pnpm exec next start -p "${LAKO_WEB_PORT:-3020}"
