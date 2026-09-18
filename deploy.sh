#!/usr/bin/env bash
#
# Samryetha 一键部署脚本（Ubuntu / pm2 / nginx，可选 SSL）
# 后端：Python(FastAPI, uv)，前端：React(express SSR)
#
# 用法：
#   ./deploy.sh                                # 用本机 IP，http
#   DOMAIN=forum.example.com ./deploy.sh       # 带域名，http
#   DOMAIN=forum.example.com SSL=1 ./deploy.sh # 域名 + certbot HTTPS
#
# 可覆盖变量：
#   DOMAIN              对外域名；缺省用本机 IP（http）
#   SSL                 0/1，是否申请 Let's Encrypt（需 DOMAIN）
#   APP_ORIGIN          前端来源校验；缺省 http://$DOMAIN
#   ALLOWED_EMAIL_DOMAINS  注册邮箱域名白名单，缺省 example.edu.cn
#   ADMIN_PASSWORD / DEV_PASSWORD  内置账号密码；缺省随机生成并打印
#   OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET  Authentik OIDC（必须成套提供）
#
# 前置要求（脚本只检查不自动安装）：python3、uv、node>=20、pnpm、pm2、nginx

set -euo pipefail

# ------------------------------------------------------------------ 变量
DOMAIN="${DOMAIN:-}"
SSL="${SSL:-0}"
APP_ORIGIN="${APP_ORIGIN:-}"
ALLOWED_EMAIL_DOMAINS="${ALLOWED_EMAIL_DOMAINS:-example.edu.cn}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
DEV_PASSWORD="${DEV_PASSWORD:-}"
OIDC_ISSUER="${OIDC_ISSUER:-}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-}"
OIDC_CLIENT_SECRET="${OIDC_CLIENT_SECRET:-}"
OIDC_REDIRECT_URI="${OIDC_REDIRECT_URI:-}"
OIDC_POST_LOGOUT_REDIRECT_URI="${OIDC_POST_LOGOUT_REDIRECT_URI:-}"
OIDC_ALLOWED_GROUPS="${OIDC_ALLOWED_GROUPS:-samryetha-users,samryetha-admins}"
OIDC_ADMIN_GROUP="${OIDC_ADMIN_GROUP:-samryetha-admins}"

# --- i18n 翻译站（可选，需真实域名启用子域） ---
I18N_DOMAIN="${I18N_DOMAIN:-}"                                   # 翻译站子域，默认 i18n.$DOMAIN
I18N_SITE_ORIGIN="${I18N_SITE_ORIGIN:-}"                         # 翻译站公网地址（也用于 i18n server CORS）
I18N_API_ORIGIN="${I18N_API_ORIGIN:-http://127.0.0.1:3002}"      # 主站 SSR 预取 i18n 的内部地址
I18N_CLIENT_ORIGIN="${I18N_CLIENT_ORIGIN:-}"                     # 注入浏览器 fetch 的公网地址（默认 https://$I18N_DOMAIN）
I18N_DATABASE_URL="${I18N_DATABASE_URL:-}"                       # i18n 自身库
I18N_AUTH_DB_URL="${I18N_AUTH_DB_URL:-}"                         # 主站库（读 samryetha_session 身份）
COOKIE_DOMAIN="${COOKIE_DOMAIN:-}"                               # 跨子域共享登录：默认 .$DOMAIN

# --- Lako 身份服务（自建 IdP；论坛启用 OIDC 后它就是登录的硬依赖） ---
LAKO_DOMAIN="${LAKO_DOMAIN:-}"                                   # 默认 auth.$DOMAIN
LAKO_ORIGIN="${LAKO_ORIGIN:-}"                                   # 公网地址，默认 https://$LAKO_DOMAIN
LAKO_API_PORT="${LAKO_API_PORT:-8000}"                           # Lako api 监听（仅本机）
LAKO_WEB_PORT="${LAKO_WEB_PORT:-3020}"                           # Lako web 监听（仅本机）
LAKO_DATABASE_URL="${LAKO_DATABASE_URL:-}"                       # 默认 SQLite，与论坛一致，免装 Postgres
LAKO_ADMIN_USERNAME="${LAKO_ADMIN_USERNAME:-}"
LAKO_ADMIN_EMAIL="${LAKO_ADMIN_EMAIL:-}"                         # 必填，create-admin 需要
LAKO_ADMIN_PASSWORD="${LAKO_ADMIN_PASSWORD:-}"                   # 留空则随机生成并打印
LAKO_SMTP_HOST="${LAKO_SMTP_HOST:-}"                             # 留空 = 只告警，邮件功能不可用
LAKO_SMTP_PORT="${LAKO_SMTP_PORT:-587}"
LAKO_SMTP_USERNAME="${LAKO_SMTP_USERNAME:-}"
LAKO_SMTP_PASSWORD="${LAKO_SMTP_PASSWORD:-}"
LAKO_SMTP_FROM="${LAKO_SMTP_FROM:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
I18N_DIR="$ROOT/i18n"
LAKO_DIR="$ROOT/lako"
LAKO_API="$LAKO_DIR/api"
LAKO_WEB="$LAKO_DIR/web"
LAKO_UI="$LAKO_DIR/packages/ui"

# ------------------------------------------------------------------ 工具
step() { printf '\n\033[1;36m[%s/10] %s\033[0m\n' "$1" "$2"; }
die() { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "缺少 $1，请先安装再运行：$2"; }

# ------------------------------------------------------------------ 1. 前置检查
step 1 "检查环境"
require uv "curl -LsSf https://astral.sh/uv/install.sh | sh"
require python3 "sudo apt install -y python3"
require node "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash - && sudo apt install -y nodejs"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 20 ] || die "需要 Node >= 20，当前 $(node -v)"
require pnpm "npm i -g pnpm"
require pm2 "npm i -g pm2"
require nginx "sudo apt install -y nginx"
echo "[ok] python3 $(python3 --version) / uv $(uv --version) / node $(node -v)"

# ------------------------------------------------------------------ 2. 解析变量
step 2 "解析配置"
IS_IP=0
if [ -z "$DOMAIN" ]; then
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$IP" ] || IP="$(hostname)"
  DOMAIN="$IP"
  IS_IP=1
fi
[ -n "$APP_ORIGIN" ] || APP_ORIGIN="http://$DOMAIN"
if [ -n "$OIDC_ISSUER$OIDC_CLIENT_ID$OIDC_CLIENT_SECRET" ]; then
  [ -n "$OIDC_ISSUER" ] && [ -n "$OIDC_CLIENT_ID" ] && [ -n "$OIDC_CLIENT_SECRET" ] \
    || die "OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET 必须成套提供"
  [ -n "$OIDC_REDIRECT_URI" ] || OIDC_REDIRECT_URI="$APP_ORIGIN/api/auth/callback"
  [ -n "$OIDC_POST_LOGOUT_REDIRECT_URI" ] || OIDC_POST_LOGOUT_REDIRECT_URI="$APP_ORIGIN/"
fi
# --- i18n 子域解析：仅真实域名启用（IP 部署跳过翻译站，避免 i18n.1.2.3.4 无意义） ---
I18N_ENABLED=0
if [ "$IS_IP" = "0" ]; then
  I18N_ENABLED=1
  [ -n "$I18N_DOMAIN" ] || I18N_DOMAIN="i18n.$DOMAIN"
  I18N_PROTO="$([ "$SSL" = "1" ] && printf 'https' || printf 'http')"
  [ -n "$I18N_SITE_ORIGIN" ] || I18N_SITE_ORIGIN="$I18N_PROTO://$I18N_DOMAIN"
  [ -n "$I18N_CLIENT_ORIGIN" ] || I18N_CLIENT_ORIGIN="$I18N_PROTO://$I18N_DOMAIN"
  [ -n "$COOKIE_DOMAIN" ] || COOKIE_DOMAIN=".$DOMAIN"
  [ -n "$I18N_DATABASE_URL" ] || I18N_DATABASE_URL="$I18N_DIR/data/i18n.db"
  [ -n "$I18N_AUTH_DB_URL" ] || I18N_AUTH_DB_URL="$BACKEND/data/app.db"
fi
# --- Lako 子域解析：需要真实域名 + HTTPS ---
# Lako 的生产校验强制 APP_ORIGIN / API_ORIGIN / OIDC_ISSUER 全部是 https，
# 所以没开 SSL 就没有能通过校验的配置，只能跳过。
LAKO_ENABLED=0
if [ "$IS_IP" = "0" ] && [ "$SSL" = "1" ]; then
  LAKO_ENABLED=1
  [ -n "$LAKO_DOMAIN" ] || LAKO_DOMAIN="auth.$DOMAIN"
  [ -n "$LAKO_ORIGIN" ] || LAKO_ORIGIN="https://$LAKO_DOMAIN"
  [ -n "$LAKO_DATABASE_URL" ] || LAKO_DATABASE_URL="sqlite+aiosqlite:///$LAKO_API/data/lako.db"
  [ -n "$LAKO_ADMIN_USERNAME" ] || LAKO_ADMIN_USERNAME="admin"
  [ -n "$LAKO_ADMIN_EMAIL" ] || LAKO_ADMIN_EMAIL="admin@$DOMAIN"
elif [ "$IS_IP" = "0" ]; then
  echo "[!] SSL=0 → 跳过 Lako（它的生产模式要求 https，见 lako/api/app/common/config.py）"
fi
if [ "$LAKO_ENABLED" = "0" ] && [ -n "$OIDC_ISSUER" ]; then
  echo "[!] 论坛配了 OIDC_ISSUER 但本次没部署 Lako——请确认那个 IdP 是别的服务在跑。"
fi
echo "  domain      : $DOMAIN"
echo "  ssl         : $SSL"
echo "  app_origin  : $APP_ORIGIN"
echo "  email_domains: $ALLOWED_EMAIL_DOMAINS"
if [ "$I18N_ENABLED" = "1" ]; then
  echo "  i18n        : $I18N_DOMAIN (client $I18N_CLIENT_ORIGIN / ssr $I18N_API_ORIGIN)"
  echo "  cookie_domain: $COOKIE_DOMAIN"
else
  echo "  i18n        : disabled（IP 部署不启用翻译站）"
fi
if [ "$LAKO_ENABLED" = "1" ]; then
  echo "  lako        : $LAKO_ORIGIN (web :$LAKO_WEB_PORT / api :$LAKO_API_PORT)"
  echo "  lako admin  : $LAKO_ADMIN_USERNAME <$LAKO_ADMIN_EMAIL>"
  [ -n "$LAKO_SMTP_HOST" ] || echo "  lako smtp   : 未配置——邮件功能不可用（启动会告警）"
else
  echo "  lako        : disabled"
fi

# ------------------------------------------------------------------ 3. 安装依赖
step 3 "安装依赖"
cd "$BACKEND"
uv sync --frozen
if [ "$I18N_ENABLED" = "1" ]; then
  cd "$I18N_DIR"
  uv sync --frozen
  cd "$I18N_DIR/site"
  pnpm install --prod=false
fi
# --- Lako 必须**先于论坛**安装 ---
# 论坛用 `file:../lako/packages/ui` 引共享包，而 pnpm 对 file: 目录依赖做的是
# **硬链接拷贝**（不是软链）。所以 frontend 的 node_modules 里是安装那一刻的 dist：
# 先装论坛再构建 ui，装进去的就是旧产物，而且不会有任何报错。
# lako/ 自己内部是 workspace:*（软链），没这个问题。
if [ "$LAKO_ENABLED" = "1" ]; then
  cd "$LAKO_API"
  uv sync --frozen
  mkdir -p "$LAKO_API/data"
  # lako/ 是个 pnpm workspace（web + examples + packages/*），在根上装一次。
  cd "$LAKO_DIR"
  pnpm install --prod=false
  cd "$LAKO_UI"
  pnpm build
fi
cd "$FRONTEND"
pnpm install --prod=false
cd "$ROOT"

# ------------------------------------------------------------------ 4. 生成 .env
step 4 "配置 .env（lako 先写，论坛的 OIDC 密钥由它发）"

# --- 4a. Lako ---
# 顺序要紧：论坛的 OIDC_CLIENT_SECRET 就是 Lako 这边的 SAMRYETHA_CLIENT_SECRET，
# 两个值必须一致，所以先落 Lako 的 .env，再把密钥喂给论坛。
LAKO_CLIENT_SECRET=""
LAKO_ENV="$LAKO_API/.env"
if [ "$LAKO_ENABLED" = "1" ]; then
  if [ -f "$LAKO_ENV" ]; then
    echo "[ok] $LAKO_ENV 已存在，跳过（保留现有配置）"
    LAKO_CLIENT_SECRET="$(grep -E '^SAMRYETHA_CLIENT_SECRET=' "$LAKO_ENV" | head -1 | cut -d= -f2- || true)"
  else
    [ -n "$LAKO_ADMIN_PASSWORD" ] || LAKO_ADMIN_PASSWORD="$(openssl rand -hex 16)"
    LAKO_CLIENT_SECRET="$(openssl rand -hex 32)"
    # PKCS#8 PEM，换行转义成**字面量** \n——Lako 侧 jwt_keys.py 会做
    # `replace("\\n", "\n")`，所以 env 里存的必须是反斜杠+n 两个字符，不是真换行。
    # heredoc 不处理反斜杠转义，所以原样写得进去。
    LAKO_JWT_KEY="$(openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null | awk '{printf "%s\\n", $0}')"
    LAKO_ALLOWED="$APP_ORIGIN"
    [ "$I18N_ENABLED" = "1" ] && LAKO_ALLOWED="$LAKO_ALLOWED,$I18N_SITE_ORIGIN"
    cat > "$LAKO_ENV" <<EOF
# --- 由 deploy.sh 生成 ---
ENVIRONMENT=production
APP_ORIGIN=$LAKO_ORIGIN
API_ORIGIN=$LAKO_ORIGIN
OIDC_ISSUER=$LAKO_ORIGIN
DATABASE_URL=$LAKO_DATABASE_URL
COOKIE_SECURE=true
JWT_PRIVATE_KEY=$LAKO_JWT_KEY
JWT_KEY_ID=lako-prod-1
CREDENTIAL_ENCRYPTION_SECRET=$(openssl rand -hex 32)
SESSION_TTL_SECONDS=2592000
AUTH_CODE_TTL_SECONDS=300
ACCESS_TOKEN_TTL_SECONDS=900
# 论坛必须在内：嵌入流的组件是**跨源带凭据**来调 Lako 的，不在名单里会被 CORS 挡掉。
ALLOWED_ORIGINS=$LAKO_ALLOWED
SAMRYETHA_CLIENT_ID=samryetha
SAMRYETHA_CLIENT_SECRET=$LAKO_CLIENT_SECRET
SAMRYETHA_REDIRECT_URIS=$APP_ORIGIN/api/auth/callback
ADMIN_IMPORT_TOKEN=$(openssl rand -hex 32)
SMTP_HOST=$LAKO_SMTP_HOST
SMTP_PORT=$LAKO_SMTP_PORT
SMTP_USERNAME=$LAKO_SMTP_USERNAME
SMTP_PASSWORD=$LAKO_SMTP_PASSWORD
SMTP_FROM=$LAKO_SMTP_FROM
EOF
    chmod 600 "$LAKO_ENV"
    echo "[+] 已生成 $LAKO_ENV"
    echo "    Lako 管理员: $LAKO_ADMIN_USERNAME / $LAKO_ADMIN_PASSWORD"
    [ -n "$LAKO_SMTP_HOST" ] || echo "    [!] 未配 SMTP：密码重置/验证/邀请邮件不会发出（启动会告警）"
  fi
  # 论坛没显式指定 IdP 时，默认就用本次部署的 Lako。
  if [ -z "$OIDC_ISSUER" ]; then
    OIDC_ISSUER="$LAKO_ORIGIN"
    OIDC_CLIENT_ID="samryetha"
    OIDC_CLIENT_SECRET="$LAKO_CLIENT_SECRET"
    OIDC_REDIRECT_URI="$APP_ORIGIN/api/auth/callback"
    OIDC_POST_LOGOUT_REDIRECT_URI="$APP_ORIGIN/"
    echo "[i] 论坛 OIDC 默认指向本次部署的 Lako：$OIDC_ISSUER"
  fi
fi

# --- 4b. 论坛 ---
ENV_FILE="$BACKEND/.env"
if [ -f "$ENV_FILE" ]; then
  echo "[ok] $ENV_FILE 已存在，跳过（保留现有配置）"
  # 已部署过的机器上，Lako 的客户端密钥可能与论坛里存的不一致——那会导致
  # 换 token 时 401。只提示，不擅自改生产 .env。
  if [ "$LAKO_ENABLED" = "1" ] && [ -n "$LAKO_CLIENT_SECRET" ]; then
    EXISTING="$(grep -E '^OIDC_CLIENT_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
    if [ -n "$EXISTING" ] && [ "$EXISTING" != "$LAKO_CLIENT_SECRET" ]; then
      echo "[!] 论坛 .env 的 OIDC_CLIENT_SECRET 与 Lako 的 SAMRYETHA_CLIENT_SECRET 不一致，"
      echo "    换 token 会 401。请手工把论坛 .env 里的那一行改成："
      echo "    OIDC_CLIENT_SECRET=$LAKO_CLIENT_SECRET"
    fi
  fi
else
  cp "$BACKEND/.env.example" "$ENV_FILE"
  [ -z "$ADMIN_PASSWORD" ] && ADMIN_PASSWORD="$(openssl rand -hex 16)"
  [ -z "$DEV_PASSWORD" ] && DEV_PASSWORD="$(openssl rand -hex 16)"
  cat >> "$ENV_FILE" <<EOF

# --- 部署覆盖（deploy.sh 写入） ---
NODE_ENV=production
APP_ORIGIN=$APP_ORIGIN
COOKIE_SECURE=$([ "$SSL" = "1" ] && printf 'true' || printf 'false')
COOKIE_DOMAIN=$COOKIE_DOMAIN
TRUST_PROXY=true
ALLOWED_EMAIL_DOMAINS=$ALLOWED_EMAIL_DOMAINS
STORAGE_SECRET=$(openssl rand -hex 32)
ADMIN_PASSWORD=$ADMIN_PASSWORD
DEV_PASSWORD=$DEV_PASSWORD
OIDC_ISSUER=$OIDC_ISSUER
OIDC_CLIENT_ID=$OIDC_CLIENT_ID
OIDC_CLIENT_SECRET=$OIDC_CLIENT_SECRET
OIDC_REDIRECT_URI=$OIDC_REDIRECT_URI
OIDC_POST_LOGOUT_REDIRECT_URI=$OIDC_POST_LOGOUT_REDIRECT_URI
OIDC_ALLOWED_GROUPS=$OIDC_ALLOWED_GROUPS
OIDC_ADMIN_GROUP=$OIDC_ADMIN_GROUP
EOF
  echo "[+] 已生成 $ENV_FILE"
  echo "    admin 密码: $ADMIN_PASSWORD"
  echo "    dev   密码: $DEV_PASSWORD"
  echo "    （请妥善保存；如需改，编辑 $ENV_FILE 后重启）"
fi

# ------------------------------------------------------------------ 5. 构建前端
step 5 "构建前端（论坛 / 翻译站 / Lako；后端 Python 无需编译）"
cd "$FRONTEND"
pnpm build
if [ "$I18N_ENABLED" = "1" ]; then
  cd "$I18N_DIR/site"
  pnpm build
fi
if [ "$LAKO_ENABLED" = "1" ]; then
  # @lako/ui 已在第 3 步构建好（必须早于论坛的 pnpm install，原因见那里）。
  cd "$LAKO_WEB"
  # next.config.ts 的 rewrite 在**构建时**求值并被烤进产物，所以这里也要给，
  # 不能只留给 pm2 的运行时环境。
  LAKO_API_INTERNAL_URL="http://127.0.0.1:$LAKO_API_PORT" pnpm build
fi
cd "$ROOT"

# ------------------------------------------------------------------ 6. Lako 库初始化
step 6 "初始化 Lako 数据库"
if [ "$LAKO_ENABLED" = "1" ]; then
  cd "$LAKO_API"
  uv run alembic upgrade head
  uv run python -m app.cli seed
  # 管理员只在第一次建：已存在时 create-admin 会撞唯一约束，忽略即可。
  uv run python -m app.cli create-admin \
    --username "$LAKO_ADMIN_USERNAME" \
    --email "$LAKO_ADMIN_EMAIL" \
    --password "$LAKO_ADMIN_PASSWORD" \
    --display-name "$LAKO_ADMIN_USERNAME" \
    || echo "[i] 管理员 $LAKO_ADMIN_USERNAME 已存在，跳过"
  cd "$ROOT"
else
  echo "[ok] 跳过（Lako 未启用）"
fi

# i18n .env + seed（仅启用时）
if [ "$I18N_ENABLED" = "1" ]; then
  I18N_ENV="$I18N_DIR/.env"
  if [ -f "$I18N_ENV" ]; then
    echo "[ok] $I18N_ENV 已存在，跳过（保留现有配置）"
  else
    cat > "$I18N_ENV" <<EOF
NODE_ENV=production
PORT=3002
APP_ORIGIN=$APP_ORIGIN
I18N_SITE_ORIGIN=$I18N_SITE_ORIGIN
I18N_DATABASE_URL=$I18N_DATABASE_URL
I18N_AUTH_DB_URL=$I18N_AUTH_DB_URL
COOKIE_SECURE=$([ "$SSL" = "1" ] && printf 'true' || printf 'false')
I18N_SUPPORTED_LOCALES=en,zh-CN,zh-TW,ja,ko,es,fr,de
I18N_SITE_DIR=$I18N_DIR/site/dist
EOF
    echo "[+] 已生成 $I18N_ENV"
  fi
  echo "[i18n] 导入 seed 翻译…"
  cd "$I18N_DIR"
  I18N_DATABASE_URL="$I18N_DATABASE_URL" uv run python seed.py
  cd "$ROOT"
fi

# ------------------------------------------------------------------ 7. pm2 启动
step 7 "pm2 启动服务"
PM2_NAMES="samryetha-backend / samryetha-frontend"
pm2 delete samryetha-backend >/dev/null 2>&1 || true
pm2 delete samryetha-frontend >/dev/null 2>&1 || true
pm2 start "$BACKEND/start.sh" --name samryetha-backend --cwd "$BACKEND"
if [ "$I18N_ENABLED" = "1" ]; then
  pm2 delete samryetha-i18n >/dev/null 2>&1 || true
  pm2 start "$I18N_DIR/start.sh" --name samryetha-i18n --cwd "$I18N_DIR"
  NODE_ENV=production API_TARGET=http://127.0.0.1:3001 I18N_API_ORIGIN="$I18N_API_ORIGIN" I18N_CLIENT_ORIGIN="$I18N_CLIENT_ORIGIN" \
    pm2 start "$FRONTEND/server.mjs" --name samryetha-frontend --cwd "$FRONTEND"
  PM2_NAMES="$PM2_NAMES / samryetha-i18n"
else
  NODE_ENV=production API_TARGET=http://127.0.0.1:3001 pm2 start "$FRONTEND/server.mjs" --name samryetha-frontend --cwd "$FRONTEND"
fi
if [ "$LAKO_ENABLED" = "1" ]; then
  pm2 delete lako-api >/dev/null 2>&1 || true
  pm2 delete lako-web >/dev/null 2>&1 || true
  LAKO_API_PORT="$LAKO_API_PORT" pm2 start "$LAKO_API/start.sh" --name lako-api --cwd "$LAKO_API"
  # LAKO_API_INTERNAL_URL 必须和构建时一致，否则 rewrite 会指到别处。
  LAKO_WEB_PORT="$LAKO_WEB_PORT" LAKO_API_INTERNAL_URL="http://127.0.0.1:$LAKO_API_PORT" \
    pm2 start "$LAKO_WEB/start.sh" --name lako-web --cwd "$LAKO_WEB"
  PM2_NAMES="$PM2_NAMES / lako-api / lako-web"
fi
pm2 save
echo "[ok] pm2 进程：$PM2_NAMES"

# ------------------------------------------------------------------ 8. nginx 配置
step 8 "配置 nginx"
NGINX_CONF="/etc/nginx/sites-available/samryetha"
NGINX_ENABLED="/etc/nginx/sites-enabled/samryetha"
sudo tee "$NGINX_CONF" > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-User "";
        proxy_set_header X-Role "";
        proxy_set_header X-Email "";
        # SSE 不缓冲
        proxy_buffering off;
        proxy_cache off;
    }
}
EOF
if [ "$I18N_ENABLED" = "1" ]; then
  NGINX_I18N_CONF="/etc/nginx/sites-available/samryetha-i18n"
  NGINX_I18N_ENABLED="/etc/nginx/sites-enabled/samryetha-i18n"
  sudo tee "$NGINX_I18N_CONF" > /dev/null <<EOF
server {
    listen 80;
    server_name $I18N_DOMAIN;

    client_max_body_size 5m;

    location / {
        proxy_pass http://127.0.0.1:3002;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  sudo ln -sf "$NGINX_I18N_CONF" "$NGINX_I18N_ENABLED"
  echo "[ok] i18n nginx 已配置：$I18N_DOMAIN → :3002"
fi
if [ "$LAKO_ENABLED" = "1" ]; then
  # Lako 的 discovery / 授权端点都由 web 这一层反代到 api，
  # 所以只需要一个 vhost 指向 web，浏览器只认这一个源。
  NGINX_LAKO_CONF="/etc/nginx/sites-available/lako"
  NGINX_LAKO_ENABLED="/etc/nginx/sites-enabled/lako"
  sudo tee "$NGINX_LAKO_CONF" > /dev/null <<EOF
server {
    listen 80;
    server_name $LAKO_DOMAIN;

    client_max_body_size 5m;

    location / {
        proxy_pass http://127.0.0.1:$LAKO_WEB_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  sudo ln -sf "$NGINX_LAKO_CONF" "$NGINX_LAKO_ENABLED"
  echo "[ok] lako nginx 已配置：$LAKO_DOMAIN → :$LAKO_WEB_PORT"
fi
sudo ln -sf "$NGINX_CONF" "$NGINX_ENABLED"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
echo "[ok] nginx 已配置并重载"

# ------------------------------------------------------------------ 9. SSL（可选）
step 9 "SSL"
if [ "$SSL" = "1" ]; then
  if [ "$IS_IP" = "1" ]; then
    echo "[!] 未提供 DOMAIN，跳过 SSL（需要真实域名才能签发证书）"
  else
    require certbot "sudo apt install -y certbot python3-certbot-nginx"
    CERTS_DOMAIN_ARGS="-d $DOMAIN"
    if [ "$I18N_ENABLED" = "1" ]; then CERTS_DOMAIN_ARGS="$CERTS_DOMAIN_ARGS -d $I18N_DOMAIN"; fi
    if [ "$LAKO_ENABLED" = "1" ]; then CERTS_DOMAIN_ARGS="$CERTS_DOMAIN_ARGS -d $LAKO_DOMAIN"; fi
    # shellcheck disable=SC2086
    sudo certbot --nginx $CERTS_DOMAIN_ARGS --redirect --non-interactive --agree-tos || true
    # shellcheck disable=SC2086
    sudo certbot --nginx $CERTS_DOMAIN_ARGS --redirect || echo "[!] certbot 交互式续跑失败，请手动执行：sudo certbot --nginx $CERTS_DOMAIN_ARGS"
    echo "[ok] HTTPS 已配置"
  fi
else
  echo "[ok] SSL=0，跳过（需要时：SSL=1 DOMAIN=... ./deploy.sh）"
fi

# ------------------------------------------------------------------ 10. 健康检查
step 10 "健康检查"
sleep 4
curl -fsS http://localhost:3001/api/health >/dev/null && echo "[ok] 后端   http://localhost:3001/api/health → 200" || die "后端健康检查失败"
curl -fsS -o /dev/null http://localhost:3000/login && echo "[ok] 前端   http://localhost:3000/login → 200" || die "前端健康检查失败"
if [ "$I18N_ENABLED" = "1" ]; then
  curl -fsS http://localhost:3002/health >/dev/null && echo "[ok] i18n   http://localhost:3002/health → 200" || die "i18n 健康检查失败"
fi
if [ "$LAKO_ENABLED" = "1" ]; then
  curl -fsS "http://127.0.0.1:$LAKO_API_PORT/health" >/dev/null \
    && echo "[ok] lako-api http://127.0.0.1:$LAKO_API_PORT/health → 200" || die "Lako api 健康检查失败"
  # 走 web 这一层，顺带验证 rewrite 把 discovery 反代到了 api——
  # 论坛登录失败最常见的原因就是这里不通。
  curl -fsS "http://127.0.0.1:$LAKO_WEB_PORT/.well-known/openid-configuration" >/dev/null \
    && echo "[ok] lako-web $LAKO_ORIGIN/.well-known/openid-configuration → 200" || die "Lako web 反代 discovery 失败"
fi

echo
echo "=============================================="
echo "  部署完成"
echo "  访问: $APP_ORIGIN"
if [ "$I18N_ENABLED" = "1" ]; then echo "  翻译站: $I18N_SITE_ORIGIN"; fi
echo "  进程:"
pm2 ls --no-color | grep -E "samryetha-(backend|frontend|i18n)"
echo "  运维: pm2 logs / pm2 restart samryetha-backend / samryetha-frontend"${I18N_ENABLED:+ / samryetha-i18n}
echo "  内置: admin / dev（密码见 $ENV_FILE，或部署时输出）"
echo "=============================================="
