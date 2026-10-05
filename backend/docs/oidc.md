# Authentik OIDC 部署与接入

## 边界

- `auth.samryetha.com`：Authentik，保存密码、MFA、身份中心会话和用户组。
- `samryetha.com`：论坛 OIDC client。callback 后只保存 `(issuer, subject)` 映射并签发论坛自己的 `samryetha_session`。
- 不设置 `.samryetha.com` Domain cookie；论坛 cookie 保持 host-only。
- OIDC token 不发送给 React，也不写 localStorage、sessionStorage 或数据库。

## Authentik Provider

在 Authentik 创建 OAuth2/OpenID Provider 和 Application：

1. Client type 选择 confidential，保存 client id 和 client secret。
2. Redirect URI 严格设置为 `https://samryetha.com/api/auth/callback`。
3. Signing key 使用非对称密钥；论坛接受 `RS256`、`PS256` 或 `ES256`。
4. 授权 scope 至少包含 `openid profile email groups`，并确认 ID token 中实际产生 `groups` claim。
5. 允许的 post-logout redirect URI 设置为 `https://samryetha.com/`。
6. 建立 `samryetha-users`、`samryetha-admins` 用户组，并把可登录用户加入至少一个准入组。

Provider 显示的 issuer 必须与 `.env` 的 `OIDC_ISSUER` 逐字符一致，包括路径和末尾 `/`。论坛还会要求 discovery 文档中的 issuer 与该值完全一致。

## 论坛配置

生产环境 `backend/.env`：

```dotenv
NODE_ENV=production
APP_ORIGIN=https://samryetha.com
COOKIE_SECURE=true
TRUST_PROXY=true

OIDC_ISSUER=https://auth.samryetha.com/application/o/samryetha/
OIDC_CLIENT_ID=<authentik-client-id>
OIDC_CLIENT_SECRET=<authentik-client-secret>
OIDC_REDIRECT_URI=https://samryetha.com/api/auth/callback
OIDC_POST_LOGOUT_REDIRECT_URI=https://samryetha.com/
OIDC_ALLOWED_GROUPS=samryetha-users,samryetha-admins
OIDC_ADMIN_GROUP=samryetha-admins
```

缺少 OIDC 配置时，论坛保持原有本地登录。生产环境一旦启用 OIDC，配置加载器会拒绝 HTTP URL和空 client secret。

## nginx

论坛仍只需将 `samryetha.com` 转发到 SSR 服务 `127.0.0.1:3000`；SSR 服务将 `/api` 转发到仅监听内网的 FastAPI `:3001`。必须保留 callback 的查询字符串和 `Set-Cookie` 响应头。不要在 nginx 注入或信任外部的 `X-User`、`X-Role`、`X-Email`。

Authentik 使用独立的 `auth.samryetha.com` server block。两个站点都启用 HTTPS，但不能配置跨子域 Cookie：

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

# 清除客户端伪造的身份头；论坛不会使用这些头鉴权。
proxy_set_header X-User "";
proxy_set_header X-Role "";
proxy_set_header X-Email "";
```

## 账号迁移行为

当前为双轨阶段：

- `GET /api/auth/login` 启动 OIDC；原有 `POST /api/auth/login` 继续支持用户名密码。
- 首次 OIDC 登录只在身份中心声明 `email_verified=true` 且论坛存量账号也已验证该 email 时自动关联。
- 其余身份创建新的 active 论坛资料；密码列写入不可知随机值，不能用于本地登录。
- 后续登录始终按 `(issuer, subject)` 找回同一论坛用户，用户名和 email 在身份中心修改不会改变关联。
- `samryetha-admins` 会提升本地角色为 `admin`；移除该组不会自动降级本地角色，降级仍由论坛管理员执行，避免覆盖本地权限治理。

正式关闭普通本地密码登录前，应先完成存量账号核对和 break-glass 管理入口建设。

## 邮箱验证与授权拦截（`OIDC_REQUIRE_VERIFIED_EMAIL`）

论坛按 `email_verified` claim 决定是"自动关联存量账号"还是"当陌生身份处理"：claim 为 false
时，一个邮箱和存量账号完全一致的老师会被当成新人——只能走认领页，或者被建出一个小号。所以
IdP 侧在**铸授权码之前**也要看这个 claim。

- 开关 `OIDC_REQUIRE_VERIFIED_EMAIL`（默认 `false`，Lako 的 `.env`）。关闭时行为与改动前一致，
  只是前端继续提示"去验证邮箱"。
- 打开后，`/oauth/authorize` 对"邮箱未验证"的会话返回 302 到 `/verify-email?return_to=…`（附带
  掩码地址），验证完成后用同一个 `return_to` 回到授权请求。嵌入流（`POST /api/oauth/authorize`）
  返回 `{"status":"verify_modified"}` 形状的 JSON，由调用方决定怎么渲染。
- **占位地址不拦**：迁移写的 `@migrated.invalid`、以及 IdP 身份没有已验证邮箱时按 subject 生成的
  `@<FAKE_EMAIL_DOMAIN>` 地址，谁也收不到信。拦下它们等于把账号锁死——这类账号应去账号页设一个
  真地址，这也是拦截页给出的出口。
- 验证入口是**验证码**（`POST /api/account/email/verify/code*`）而不是邮件链接：拦截的这条路上
  手里有一个正在进行的授权请求，跟着链接跳到 `/verify` 会把 `return_to` 丢掉。
- 已知影响：Lako 本地注册（`POST /api/auth/register`）建出的邮箱是 `verified=false`，开这个开关后
  这类账号也要先验证才能授权；启用前请确认客户端能走完验证。

## 验收

1. 未登录访问 `/login`，页面优先显示“使用 Samryetha 账号登录”。
2. callback 后浏览器只有 `samryetha_session`，Application/Storage 中没有 OIDC token。
3. 修改 Authentik email/username 后再次登录，论坛 user id 不变。
4. 不在准入组的用户 callback 返回 403。
5. `samryetha-admins` 用户能登录，但 `/api/admin/*` 仍由论坛后端角色和能力矩阵授权。
6. 登出经 `/api/auth/oidc/logout` 同时清除论坛会话并跳转 Authentik end-session endpoint。
