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

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

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
echo "  domain      : $DOMAIN"
echo "  ssl         : $SSL"
echo "  app_origin  : $APP_ORIGIN"
echo "  email_domains: $ALLOWED_EMAIL_DOMAINS"

# ------------------------------------------------------------------ 3. 安装依赖
step 3 "安装依赖"
cd "$BACKEND"
uv sync --frozen
cd "$FRONTEND"
pnpm install --prod=false
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
cd "$ROOT"

# ------------------------------------------------------------------ 6. pm2 启动
step 6 "pm2 启动服务"
pm2 delete samryetha-backend >/dev/null 2>&1 || true
pm2 delete samryetha-frontend >/dev/null 2>&1 || true
pm2 start "$BACKEND/start.sh" --name samryetha-backend --cwd "$BACKEND"
NODE_ENV=production API_TARGET=http://127.0.0.1:3001 pm2 start "$FRONTEND/server.mjs" --name samryetha-frontend --cwd "$FRONTEND"
pm2 save
echo "[ok] pm2 进程：samryetha-backend / samryetha-frontend"

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
    sudo certbot --nginx -d "$DOMAIN" --redirect --non-interactive --agree-tos || true
    sudo certbot --nginx -d "$DOMAIN" --redirect || echo "[!] certbot 交互式续跑失败，请手动执行：sudo certbot --nginx -d $DOMAIN"
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

echo
echo "=============================================="
echo "  部署完成"
echo "  访问: $APP_ORIGIN"
echo "  进程:"
pm2 ls --no-color | grep -E "samryetha-(backend|frontend)"
echo "  运维: pm2 logs / pm2 restart samryetha-backend / samryetha-frontend"
echo "  内置: admin / dev（密码见 $ENV_FILE，或部署时输出）"
echo "=============================================="
