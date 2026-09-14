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

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
I18N_DIR="$ROOT/i18n"

# ------------------------------------------------------------------ 工具
step() { printf '\n\033[1;36m[%s/9] %s\033[0m\n' "$1" "$2"; }
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

# ------------------------------------------------------------------ 3. 安装依赖
step 3 "安装依赖"
cd "$BACKEND"
uv sync --frozen
cd "$FRONTEND"
pnpm install --prod=false
if [ "$I18N_ENABLED" = "1" ]; then
  cd "$I18N_DIR"
  uv sync --frozen
  cd "$I18N_DIR/site"
  pnpm install --prod=false
fi
cd "$ROOT"

# ------------------------------------------------------------------ 4. 生成 .env
step 4 "配置 backend/.env"
ENV_FILE="$BACKEND/.env"
if [ -f "$ENV_FILE" ]; then
  echo "[ok] $ENV_FILE 已存在，跳过（保留现有配置）"
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
step 5 "构建前端（后端 Python 无需编译）"
cd "$FRONTEND"
pnpm build
if [ "$I18N_ENABLED" = "1" ]; then
  cd "$I18N_DIR/site"
  pnpm build
fi
cd "$ROOT"

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

# ------------------------------------------------------------------ 6. pm2 启动
step 6 "pm2 启动服务"
pm2 delete samryetha-backend >/dev/null 2>&1 || true
pm2 delete samryetha-frontend >/dev/null 2>&1 || true
pm2 start "$BACKEND/start.sh" --name samryetha-backend --cwd "$BACKEND"
if [ "$I18N_ENABLED" = "1" ]; then
  pm2 delete samryetha-i18n >/dev/null 2>&1 || true
  pm2 start "$I18N_DIR/start.sh" --name samryetha-i18n --cwd "$I18N_DIR"
  NODE_ENV=production API_TARGET=http://127.0.0.1:3001 I18N_API_ORIGIN="$I18N_API_ORIGIN" I18N_CLIENT_ORIGIN="$I18N_CLIENT_ORIGIN" \
    pm2 start "$FRONTEND/server.mjs" --name samryetha-frontend --cwd "$FRONTEND"
  pm2 save
  echo "[ok] pm2 进程：samryetha-backend / samryetha-frontend / samryetha-i18n"
else
  NODE_ENV=production API_TARGET=http://127.0.0.1:3001 pm2 start "$FRONTEND/server.mjs" --name samryetha-frontend --cwd "$FRONTEND"
  pm2 save
  echo "[ok] pm2 进程：samryetha-backend / samryetha-frontend（i18n 翻译站未启用）"
fi

# ------------------------------------------------------------------ 7. nginx 配置
step 7 "配置 nginx"
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
sudo ln -sf "$NGINX_CONF" "$NGINX_ENABLED"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
echo "[ok] nginx 已配置并重载"

# ------------------------------------------------------------------ 8. SSL（可选）
step 8 "SSL"
if [ "$SSL" = "1" ]; then
  if [ "$IS_IP" = "1" ]; then
    echo "[!] 未提供 DOMAIN，跳过 SSL（需要真实域名才能签发证书）"
  else
    require certbot "sudo apt install -y certbot python3-certbot-nginx"
    CERTS_DOMAIN_ARGS="-d $DOMAIN"
    if [ "$I18N_ENABLED" = "1" ]; then CERTS_DOMAIN_ARGS="$CERTS_DOMAIN_ARGS -d $I18N_DOMAIN"; fi
    # shellcheck disable=SC2086
    sudo certbot --nginx $CERTS_DOMAIN_ARGS --redirect --non-interactive --agree-tos || true
    # shellcheck disable=SC2086
    sudo certbot --nginx $CERTS_DOMAIN_ARGS --redirect || echo "[!] certbot 交互式续跑失败，请手动执行：sudo certbot --nginx $CERTS_DOMAIN_ARGS"
    echo "[ok] HTTPS 已配置"
  fi
else
  echo "[ok] SSL=0，跳过（需要时：SSL=1 DOMAIN=... ./deploy.sh）"
fi

# ------------------------------------------------------------------ 9. 健康检查
step 9 "健康检查"
sleep 4
curl -fsS http://localhost:3001/api/health >/dev/null && echo "[ok] 后端   http://localhost:3001/api/health → 200" || die "后端健康检查失败"
curl -fsS -o /dev/null http://localhost:3000/login && echo "[ok] 前端   http://localhost:3000/login → 200" || die "前端健康检查失败"
if [ "$I18N_ENABLED" = "1" ]; then
  curl -fsS http://localhost:3002/health >/dev/null && echo "[ok] i18n   http://localhost:3002/health → 200" || die "i18n 健康检查失败"
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
