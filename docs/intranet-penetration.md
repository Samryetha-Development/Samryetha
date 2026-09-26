# 内网穿透指南（给其它服务开公网入口）

> 适用：本机 / 内网机器上跑的服务（论坛、Lako、以后加的任何服务），需要被公网访问时用。
> 你已经有自己带公网 IP 的服务器（SMTP 就跑在上面），所以**首选 frp 自建**，零费用、域名和端口完全自己说了算。

## 1. 什么时候需要穿透

| 场景 | 说明 |
|---|---|
| OAuth 回调 | IdP（Authentik / Lako）要求回调地址是**公网可达的 HTTPS**，如 `https://samryetha.com/api/auth/callback`；本地 `localhost` 收不到 |
| 手机/平板联调 | 和 Mac 不在同一局域网时测扫码登录、OAuth 跳转 |
| 给别人演示 | 发个链接即可，不用部署 |
| Webhook 接收 | 第三方回调打到本地服务 |

如果只是**自己私用、不想暴露公网**，直接看第 5 节 Tailscale，比穿透更合适。

## 2. 方案对比

| 方案 | 费用 | 要公网服务器 | HTTPS | 适合 |
|---|---|---|---|---|
| **frp（推荐）** | 免费（用自己的服务器） | 要（你有） | 自己配 nginx + certbot | 长期、稳定、多服务 |
| cloudflared | 免费 | 不要（域名需托管 Cloudflare） | 自动 | 无服务器 / 快速上线 |
| ngrok | 免费版限流 + 随机域名 | 不要 | 自动 | 临时演示 10 分钟 |
| Tailscale | 免费（个人） | 不要 | 内网而已 | 仅自己/组内访问，不暴露公网 |
| SSH 反向隧道 | 免费 | 要 | 自己配 | 临时救急 |

## 3. 方案一：frp（推荐，长期用）

架构：`你的服务（本机） --frpc--> 公网服务器 frps:7000 --nginx--> https://xxx.yourdomain.com`

### 3.1 服务端（你的公网服务器，一次配好）

```bash
# 下载 frp（以 v0.60+ 为例，注意架构 amd64/arm64）
wget https://github.com/fatedier/frp/releases/download/v0.61.0/frp_0.61.0_linux_amd64.tar.gz
tar xzf frp_0.61.0_*.tar.gz && cd frp_0.61.0_*
```

`frps.ini`（服务端只做两件事：收隧道 + 给各服务分端口）：

```ini
[common]
bind_port = 7000
# dashboard 可选，设了就加 auth
dashboard_port = 7500
dashboard_user = admin
dashboard_pwd = <16位以上随机串>
# 下面 vhost_http_port 给 HTTP 类服务复用 80/443 经由 nginx 分发
vhost_http_port = 8080
token = <32位以上随机串，frpc 用同一 token>
```

systemd 常驻（`/etc/systemd/system/frps.service`）：

```ini
[Unit]
Description=frps
After=network.target
[Service]
ExecStart=/opt/frp/frps -c /opt/frp/frps.ini
Restart=always
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now frps
# 安全组/防火墙放行：7000（隧道）、8080（http 入口，可只允许本机 nginx）、7500（dashboard，建议只内网）
```

### 3.2 本机（要暴露的服务旁跑 frpc）

`frpc.ini` 示例（按需增减 `[xxx]` 段；**一律走 127.0.0.1，不要绑 0.0.0.0**）：

```ini
[common]
server_addr = <你的服务器公网IP或域名>
server_port = 7000
token = <和服务端同一串>

# —— 论坛前端（SSR，同时代理了后端 /api，只暴露这一个即可）——
[forum-web]
type = http
local_ip = 127.0.0.1
local_port = 3000
custom_domains = forum.yourdomain.com

# —— Lako 账号页（如需对外）——
[lako-web]
type = http
local_ip = 127.0.0.1
local_port = 3000
custom_domains = auth.yourdomain.com
```

> ⚠️ 端口冲突注意：论坛前端默认 `:3000`，Lako web 默认也是 `:3000`。
> 同一台机器同时跑时，把其中一个改端口（`PORT=3001 pnpm dev` 或改 compose 映射如 `"3001:3000"`），再对应改上面的 `local_port`。
> 数据库（Postgres/SQLite 文件）**永远不要**加穿透段。

本机同样用 systemd 常驻，或开发时前台跑 `frpc -c frpc.ini`。

### 3.3 HTTPS（OAuth 回调强制要求）

我们的 OIDC 校验在 production 会 fail-fast 拒绝非 HTTPS（`config.py`），所以公网入口必须 HTTPS。服务器上 nginx 参考（`deploy.sh` 里已有现成模板逻辑，可直接复用思路）：

```nginx
server {
  listen 443 ssl;
  server_name forum.yourdomain.com;
  ssl_certificate /etc/letsencrypt/live/forum.yourdomain.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/forum.yourdomain.com/privkey.pem;

  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    # SSE 长连接（扫码登录等待、通知流）必须关缓冲，否则事件会被憋住
    proxy_buffering off;
    proxy_read_timeout 300s;
  }
}
```

certbot 一条命令拿证（参考 `deploy.sh SSL=1` 的做法）：
```bash
sudo certbot --nginx -d forum.yourdomain.com -d auth.yourdomain.com
```

### 3.4 穿透后必须同步改的配置（OAuth 相关，最容易踩坑）

穿透只是通了网络，身份链路认的是**精确字符串**，以下必须一起改，否则登录 400/校验失败：

| 位置 | 改什么 |
|---|---|
| IdP client 注册（Authentik/Lako 后台） | Redirect URI 改成公网地址，如 `https://samryetha.com/api/auth/callback`（**精确匹配**，多一个 `/` 都不行）；Lako 还要在 OAuth client 里登记 |
| 论坛 `backend/.env` | `OIDC_ISSUER`（必须和 discovery 文档里的 issuer 逐字符一致）、`OIDC_REDIRECT_URI`、`APP_ORIGIN=https://forum.yourdomain.com`、`COOKIE_SECURE=true` |
| Lako `api/.env` | `APP_ORIGIN`、`API_ORIGIN`、`OIDC_ISSUER`、`COOKIE_SECURE=true`，生产还会强制 `JWT_PRIVATE_KEY`、`SAMRYETHA_CLIENT_SECRET`、`SMTP_HOST` |
| 前端 | `server.mjs` 的 `API_TARGET` 指向可达的后端地址 |

改完重启两边服务，用**无痕窗口**走一遍：OAuth 登录 → 登出 → 扫码登录（看 SSE 有没有被某层缓冲憋住）。

## 4. 方案二：cloudflared（没服务器/想最快）

```bash
# 本机一次登录（只需一次）
cloudflared tunnel login
# 建隧道 + 配域名（域名需已托管在 Cloudflare）
cloudflared tunnel create samryetha-dev
cloudflared tunnel route dns samryetha-dev forum-dev.yourdomain.com
# 启动（config.yml 里把服务映射好）
cloudflared tunnel --config ~/.cloudflared/config.yml run samryetha-dev
```

`config.yml`：
```yaml
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: forum-dev.yourdomain.com
    service: http://localhost:3000
    originRequest: { disableChunkedEncoding: true }  # SSE 友好
  - service: http_status:404
```
HTTPS 自动搞定，同样要按 3.4 改 OAuth 相关配置。

## 5. 只想自己用：Tailscale（不推荐穿透时看这节）

手机/平板/服务器装 Tailscale 登录同一账号，直接用内网名访问（如 `http://macbook:3000`），**不经过公网、无需 HTTPS 改配置**（但注意：走 http 时 OIDC 回调若配的是 https 域名会跳出去，联调 OAuth 还是建议用上面的穿透 + 公网域名）。

## 6. 安全清单（穿透即暴露，逐条过）

- [ ] frp `token` 用 32 位以上随机串；dashboard 不对外或加 auth + 防火墙限制
- [ ] **只暴露 Web 端口**：Postgres、SQLite 文件目录、`/health` 以外的运维端口一律不穿
- [ ] `ADMIN_IMPORT_TOKEN`、`EMERGENCY_LOGIN_TOKEN`、`SAMRYETHA_CLIENT_SECRET` 等保密串走环境变量/secret 管理，不进仓库不进聊天记录
- [ ] 扫码/重置等邮件链接的 TTL 保持短（默认 2min/1h/7d 见代码），公网环境不要调大
- [ ] 定期看 Lako `audit_events` + 后端 moderation 日志，尤其是 `admin.*` 和登录失败暴涨
- [ ] 域名续期：certbot timer 是否正常（`systemctl list-timers | grep certbot`）

## 7. 排错速查

| 现象 | 查什么 |
|---|---|
| 隧道通但 502 | 本地服务是否真在监听（`lsof -i :3000`），frpc `local_ip` 是否 127.0.0.1，端口是否撞车（论坛/Lako web 都是 3000） |
| OAuth 回调 400invalid | Redirect URI 与 IdP 登记逐字符比对；`OIDC_ISSUER` 与 discovery 的 issuer 比对（含末尾 `/`） |
| 扫码一直转圈/收不到批准 | 某一层把 SSE 缓冲了：nginx `proxy_buffering off`、frp 用 `http` 类型直透、Cloudflare 默认 OK；浏览器 Network 看 `/qr/wait` 是否 pending |
| frpc 连不上 | 服务器 7000 端口防火墙/安全组、token 两边一致、服务端日志 |
| 登录后立刻掉线 | `COOKIE_SECURE=true` 但走的是 http（cookie 写不上）；或 `APP_ORIGIN` 与实际访问域名不一致 |
