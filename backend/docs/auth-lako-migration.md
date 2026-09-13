# 认证统一到 Lako 的迁移设计

目标：**所有认证都走 Lako，论坛不再参与认证。**

本文只描述设计与迁移路径，不含代码改动。配套的操作手册见 [`oauth-migration.md`](./oauth-migration.md)（论坛用户 → Lako 的数据迁移）。

---

## 1. 现状与目标

### 1.1 现状：论坛才是认证的主人

```
                    ┌──────────────────────────────┐
   浏览器 ──────────▶│  论坛后端 (3001)             │
                    │  · 发 samryetha_session      │
                    │  · sessions 表（SQLite）     │
                    │  · 密码 / QR / claim 登录     │
                    └───────────┬──────────────────┘
                                │ 读同一份 sessions + users
                                ▼
                    ┌──────────────────────────────┐
                    │  i18n 服务 (3002)            │
                    │  · 复制 cookie 名并裸 SQL 校验 │
                    └──────────────────────────────┘

                    ┌──────────────────────────────┐
   论坛 ──OIDC─────▶│  Lako (4010 / 8000)          │
                    │  · 只发一次性 code + ID token │
                    │  · 自己的 lako_session 是 host-only，别人读不到 │
                    └──────────────────────────────┘
```

关键证据：

| 事实 | 位置 |
|---|---|
| 论坛发会话 cookie | `backend/src/samryetha/security.py:22` (`SESSION_COOKIE = "samryetha_session"`) |
| 会话存在论坛自己的表 | `backend/src/samryetha/schema.py:284-296` |
| i18n 复制了 cookie 名 | `i18n/src/i18n_svc/security.py:20` |
| i18n 直接读论坛的库 | `i18n/src/i18n_svc/security.py:36-49`（`sessions JOIN users` 裸 SQL） |
| 翻译站没有会话，点登录跳论坛 | `i18n/site/src/App.tsx:15-17` |
| Lako 的 session 是 host-only | `lako/api/app/authentication/routes.py:38-67`（`set_cookie` 无 `domain=`） |

也就是说：Lako 现在只是个"验密码的"，**身份的权威方始终是论坛**。

### 1.2 目标

```
   所有服务 ──▶ Lako（唯一发会话的地方）
                  │
                  │  lako_session（父域，或由各服务在线校验）
                  ▼
   论坛 / i18n / 翻译站：只校验，不签发
```

论坛不再有自己的登录表单、密码表、`sessions` 表；密码、注册、扫码全部只在 Lako。

### 1.3 分两个阶段落地

| 阶段 | 内容 | 会话归谁 | 风险 |
|---|---|---|---|
| **一** | 统一登录入口，论坛登录页消失 | 论坛（过渡） | 低，纯前端 + 一处白名单 |
| **二** | 会话归 Lako，论坛/i18n 只校验 | Lako | 高，动三边 + 数据迁移 |

阶段一先解决"点登录掉到论坛页"的直观问题，且完全可逆；阶段二才是真正的"不牵扯论坛"。

---

## 2. Lako 现在当不了权威方：四个缺口

这一节是阶段二的前置条件，必须先补齐。

| 缺口 | 现状 | 影响 |
|---|---|---|
| **没有 `cookie_domain`** | `lako/api/app/authentication/routes.py:38-67` 的 `set_authenticated_cookies` 不传 `domain=`；`lako/api/app/common/config.py` 里根本没有这个设置项 | `lako_session` 只能被 Lako 自己读到，别的子域拿不到 |
| **没有会话校验接口** | 只有 `/oauth/userinfo`（`lako/api/app/oauth/routes.py:263-280`），它认的是**不透明 access token**，不是会话 cookie；discovery 里没有 introspection（`routes.py:91-107`） | 别的服务无法确认"这个会话还有效吗" |
| **没有登出广播** | discovery 无 `end_session_endpoint`；`POST /api/sessions/logout` 只清 Lako 自己的 cookie（`lako/api/app/sessions/routes.py:17-34`） | Lako 登出后，论坛/i18n 的会话仍活着 |
| **会话活不长且不能续** | `access_token_ttl_seconds = 900`；`/oauth/token` 只支持 `authorization_code`（`routes.py:189-260`），**没有 refresh grant** | 不能拿 token 当会话用；900 秒后必然 401 |

> ⚠️ **明确禁止**：不要用 `/oauth/userinfo` 做每请求的会话校验。它是为 OAuth access token 设计的，900 秒过期且无法续期，接到会话校验上会导致用户每 15 分钟被踢一次。

---

## 3. 阶段一：统一登录入口

**目标**：不管从哪个入口点登录，看到的都是 Lako；论坛那张登录页（密码 + 注册 + 扫码）消失。会话归属暂不变更，因此完全可逆。

### 3.1 后端

**`backend/src/samryetha/oidc.py:42-46` `safe_return_to()`**

现在只接受 `/` 开头的站内路径：

```python
if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
    return "/"
return value
```

翻译站要"签完回自己页面"，就得放行一个**站外** origin。安全规则必须收紧到：

- 新增配置项（后端目前**一条 i18n 相关配置都没有**），例如 `SIGNIN_RETURN_ORIGINS=https://i18n.samryetha.com`
- 只接受**精确 origin 匹配**（scheme + host + port 完全相同），**禁止**通配符、禁止后缀匹配（`https://i18n.samryetha.com.evil.com` 必须被拒）
- 继续拒绝 `//host`（协议相对）、反斜杠、以及任何含控制字符（CR/LF）的值
- 不在名单内 → 回落 `/`
- 名单只从 env 读，不落库、不由请求参数决定

**`backend/src/samryetha/routers/auth.py:129`**

```python
response = RedirectResponse(settings.app_origin.rstrip("/") + transaction["return_to"], status_code=302)
```

这行是直接字符串拼接。`safe_return_to` 一旦允许站外绝对 URL，**这行必须同步改**（区分"站内路径 → 拼 app_origin"和"站外白名单 origin → 原样跳"），否则会拼出 `https://samryetha.comhttps://i18n...` 这种畸形地址。

**`backend/src/samryetha/routers/auth.py:358-367` `_issue_session_cookie`**

这个 helper 漏了 `domain=settings.cookie_domain`，而另外两处写 cookie（`routers/auth.py:139-148`、`:450-459`）都有。它被 QR 兑换、claim、claim-new、emergency 四条路径调用——跨子域部署时这四条会发出 host-only cookie，导致登录"看起来成功但跨子域不认"。顺手补上。

### 3.2 论坛前端

**`frontend/src/login-page.tsx`**

改成薄壳：去掉密码表单、注册、扫码。保留第 239 行那个入口：

```jsx
<a className="login-primary login-oidc" href="/api/auth/login?returnTo=%2F">…</a>
```

即整页 `/login` 只剩"去 Lako 登录"这一个动作。

**不要顺手把弹层也改掉。**`frontend/src/auth-modal.tsx:1-10` 的注释写明弹层就是为了解决"登录完被甩走"的问题（Lako 跑在 iframe 里，父窗口 SPA 状态原样保留）。站内触发 → 弹层；直接访问 `/login` → 整页跳。两条路径各司其职，本阶段只统一"终点是 Lako"。

### 3.3 翻译站

`i18n/site/src/App.tsx`：

| 现在 | 改成 |
|---|---|
| `:15-17 handleSignIn()` = `window.location.assign(${MAIN_ORIGIN}/login)` | 走论坛 OIDC 入口，并带**回 5200** 的 `returnTo`（依赖 3.1 的白名单） |
| `:19-22 handleSignOut()` = `window.location.assign(MAIN_ORIGIN)`（只是跳首页，**没有真登出**） | 调真正的登出端点，再回翻译站 |

### 3.4 验证

1. 从论坛（3000）和翻译站（5200）分别点登录 → 都直达 Lako，签完各自回原位
2. 直接访问 `http://localhost:3000/login` → 不再出现论坛密码表单
3. **安全用例（必测，全部应回落到 `/`）**：
   - `returnTo=https://evil.com`
   - `returnTo=//evil.com`
   - `returnTo=/\evil.com`
   - `returnTo=https://i18n.samryetha.com.evil.com`（验证不是后缀匹配）
   - `returnTo=https://i18n.samryetha.com%0d%0aSet-Cookie:x`
4. `COOKIE_DOMAIN` 设为跨子域值时，QR 兑换的 `Set-Cookie` 必须带 `Domain=`

---

## 4. 阶段二：会话归 Lako

### 4.1 校验机制选型

两个候选：

| 维度 | (a) 在线校验：每请求问 Lako | (b) 签名会话令牌：JWKS 离线验签 |
|---|---|---|
| 撤销延迟 | ≈ 缓存 TTL（建议 60s，可配 stale-grace） | = 令牌 TTL（要等它过期） |
| 可用性 | Lako 是硬依赖；靠 stale 缓存缓解 | 完全独立，Lako 挂了论坛照常 |
| 实现成本 | 低：Lako 已有现成的会话判定逻辑，包个端点即可 | 中高：Lako 现在只签 `id_token`，access token 是不透明的；要塞会话进 JWT 得另做签发/轮换/kid |
| 权限新鲜度 | `groups`/`status` 永远实时 | 过期前是陈旧的：降权/封禁不即时 |
| 密钥风险 | 无 | Lako 签名密钥泄露 = 可伪造任意会话 |

**选 (a) 在线校验 + 有界陈旧缓存（stale-while-error）**，理由：

1. 所有服务都在同一台主机（43.161.252.86），走 loopback 约 1ms，**性能不构成选 (b) 的理由**
2. 论坛现有的不变量是"封禁即失效"（`backend/src/samryetha/deps.py:61-63` 会自动解封过期封禁），(b) 的陈旧窗口会破坏它
3. 权限今后从 Lako 的 `groups` 派生，`groups` 的实时性直接关系越权风险
4. 实现风险最低：`lako/api/app/sessions/dependencies.py:25-44` 已经有完整的会话有效性判定，包一层端点即可

**但要写清代价**：Lako 成为每一次请求的硬依赖。缓解手段是 stale 缓存（≤5 分钟窗口），且**写操作强制新鲜校验**，失败时 fail-closed（拒绝，而不是放行）。

**明确否掉**：让论坛/i18n 直连 Lako 的数据库。生产 Lako 是 Docker 里的 PostgreSQL（`lako/docker-compose.prod.yml`），论坛和 i18n 是 SQLite 文件——跨引擎读不了；即便能读，也会把 Lako 的内部 schema 变成公共契约。

> 顺带说明：i18n 现在能"读论坛的库"认出用户，纯粹是因为两者在同一台机器上共用同一个 SQLite 文件（`deploy.sh` 里 `I18N_AUTH_DB_URL=$BACKEND/data/app.db`）。这个便利在 Lako 上是复现不了的。

### 4.2 Lako 要补的接口契约

**(A) `cookie_domain`**

`lako/api/app/common/config.py` 新增 `cookie_domain: str = ""`，写进 `set_authenticated_cookies`（`lako/api/app/authentication/routes.py:38-67`）与所有删除 cookie 的地方（`lako/api/app/sessions/routes.py:32-33` 等），保持三处一致。

> ⚠️ 这个设置会同时影响 `lako_session` / `lako_device` / `lako_csrf` 三个 cookie。`lako_csrf` 是**非 HttpOnly** 的双提交 cookie，放宽到父域后所有子域都能读到它——设计上可接受，但要在文档里显式记录。

**(B) 会话校验端点（新）**

```
POST /api/service/session/validate
Authorization: Bearer <SERVICE_TOKEN>          # 服务间共享密钥，fail-closed
Content-Type: application/json

{ "session_token": "<lako_session 的原始值>" }
```

响应：

```jsonc
// 200 —— 会话有效
{
  "sub": "…uuid…",              // Lako 用户主键，跨客户端稳定
  "status": "ACTIVE",
  "groups": ["samryetha-users"],
  "expires_at": 1790000000,
  "session_id": "…"
}

// 401 —— 无效/过期/已撤销，code 区分原因
{ "error": { "code": "SESSION_EXPIRED" } }     // 或 SESSION_REVOKED / SESSION_NOT_FOUND
```

要点：

- 入参是**原始 cookie 值**，服务端按 `token_hash` 查（对齐 `lako/api/app/sessions/dependencies.py:25-44` 的现有判定：拒绝 `revoked_at` 非空与 `expires_at` 过期）
- 鉴权用独立的服务 token（类似已有的 `ADMIN_IMPORT_TOKEN`，见 `lako/api/app/admin/auth.py:13-20`），**不能用公开端点**
- 端点不应返回任何密码/凭据材料
- 调用方（论坛/i18n）侧缓存：TTL 60s；Lako 不可达时允许 stale 最长 300s，**但写操作不走 stale**

**(C) `end_session_endpoint`**

- 加进 discovery 文档（`lako/api/app/oauth/routes.py:91-107`）
- 需要给 OAuth client 增加 `post_logout_redirect_uri` 白名单：现在 `OAuthClient` 模型（`lako/api/app/common/models.py`）没有该字段，seed 只种 redirect_uri（`lako/api/app/authorization/service_seed.py:42-61`）。新增一张 `oauth_client_post_logout_uris` 表，seed 时读一个 `samryetha_post_logout_uris` 配置
- 会话 cookie 用 GET 导航带过去（浏览器导航场景），**不能**复用 `POST /api/sessions/logout`（它要求 CSRF 双提交，见 `lako/api/app/security/csrf.py`）。GET 端点的安全边界 = `client_id` + 精确 redirect 白名单

**好消息**：论坛这边已经对接好了。`backend/src/samryetha/oidc.py:200-208` 的 `end_session_url()` 会从 discovery 读 `end_session_endpoint`，`routers/auth.py:163-167` 已经在 try/except 里调用它。**Lako 一广播这个端点，论坛登出自动生效**，论坛侧无需改动。

**(D) 新客户端**

OAuth client 目前是**单客户端写死**的（`config.py:23-28` 的 `samryetha_client_id` + `service_seed.py:42-61`），没有 admin/API 建档入口。如果将来翻译站要注册成独立客户端，只能靠改配置 + 重跑 seed。设计上建议**不加**，让翻译站继续复用论坛的会话（见 4.4）。

### 4.3 论坛侧删改清单

> ⚠️ **`sessions` 表必须留到最后一个部署再删**（见第 6 节回滚策略）。

**删除**

| 目标 | 位置 |
|---|---|
| 会话读写原语 | `backend/src/samryetha/security.py`：`new_session_token` / `create_session` / `get_session_user` / `delete_session` / `delete_user_sessions` |
| 密码原语 | `security.py`：`hash_password` / `verify_password`（随密码链路一起下线） |
| 会话签发点 | `oidc.py:383-388`（藏在 `_finish_login` 内）；`routers/auth.py`: QR 兑换 `:348-353`、密码登录 `:450-459`、emergency `:542-547`，以及 `_issue_session_cookie:358-367` |
| 密码/注册/恢复端点 | `routers/auth.py`: `register:422-433`、`login:436-460`、`change-password:485-495`、`forgot-password:498-510`、`reset-password:513-518`、`emergency-login:521-549` |
| 扫码登录 | `routers/auth.py:251-355` 全部 + `backend/src/samryetha/qr_login.py` |
| 认领流程（迁移跑完后） | `oidc.py:434-555` + `schema.py:327-340`（`oidc_claim_tickets`）+ `routers/auth.py:370-419` + `frontend/src/claim-page.tsx` |
| `sessions` 表 | `schema.py:284-296` 及表清单项（**最后一步**） |

**改动**

`backend/src/samryetha/deps.py:54-64 get_current_user` 从"查本地 sessions 表"改为：

```
读 lako_session cookie
  → 调 Lako 校验（带 60s 缓存 / 300s stale）
  → 用 sub 查 oidc_identities 定位本地 users 行
  → 叠加论坛侧状态（封禁到期等，见 5.2）
  → 找不到映射 → 当游客
```

`backend/src/samryetha/routers/realtime.py:27-43` 复用同一个校验器，不要另写一份。

**不要删**：`oidc_identities` 表（`schema.py:298-309`）。它从"首次 OAuth 登录的映射缓存"升级为**论坛的永久身份链接表**（Lako `sub` → 论坛 `users.id`）。

### 4.4 i18n 侧删改清单

**删除**：`i18n/src/i18n_svc/security.py` 整个（复制来的 cookie 名 + 裸 SQL 校验）、`db.py:109-129` 的 `AuthDatabase` 及其 SQLite-only 分支（`db.py:28-53`）、`config.py` 的 `auth_db_url`/`effective_auth_db_url`、`deploy.sh` 注入的 `I18N_AUTH_DB_URL`。

**新增**：一个调 Lako 校验端点的 HTTP 客户端。注意 **i18n 运行时依赖里现在没有 httpx**（`i18n/pyproject.toml` 只在 dev 组里有），需要加。

**⚠️ 隐藏的类型断裂**：i18n 的 `submissions.submitter_id` / `reviewer_id` 是**本地整数**（`i18n/src/i18n_svc/schema.py`），而 Lako 的 `sub` 是 UUID 字符串。阶段二后 `CurrentUser.id` 会变成字符串，直接写入会类型不符，历史数据也对不上。

建议：i18n 新增一张 `i18n_users(lako_sub TEXT PRIMARY KEY, legacy_user_id INT)` 映射表兜住，不动历史 submissions 数据。**这是本次设计里最容易被忽略的一处**。

---

## 5. 身份与角色迁移

### 5.1 role 映射

| 论坛 `users.role` | Lako group | 状态 |
|---|---|---|
| `admin` | `samryetha-admins` | 已在 seed（`lako/api/app/authorization/service_seed.py:44-47`） |
| `student` | `samryetha-users` | 已在 seed |
| `moderator` | `samryetha-moderators` | **不存在，需要新增** |

需要改两处：
- `lako/api/app/authorization/service_seed.py:44` 的组名列表追加 `samryetha-moderators`
- `backend/scripts/migrate_users_to_lako.py:192` 现在只传 `admin = (role == "admin")` 一个布尔，需要把 moderator 也传过去，并在 Lako 侧 `_converge_flags` 里映射到对应 Role

### 5.2 status：论坛的"临时封禁"要留在论坛

| 论坛 `users.status` | Lako |
|---|---|
| `active` | `ACTIVE` |
| `banned` / `deactivated` | 非 ACTIVE（Lako 会拒绝其会话） |
| `pending` | 需决定（论坛的注册审核状态，Lako 无对应概念） |

**注意**：论坛有"带过期时间的临时封禁 + 到期自动解封"（`backend/src/samryetha/deps.py:61-63` 调 `moderation.lift_ban_if_expired`），Lako 的 `UserStatus` 没有过期概念。若把 status 权威整体交给 Lako，这个能力会丢失。

建议**保留为论坛侧 overlay**：解析出本地 `users` 行后继续跑 `lift_ban_if_expired`，论坛仍是"封禁到期"的权威，Lako 只管"账号是否存在/是否被彻底禁用"。改动最小，且不牺牲现有能力。

### 5.3 密码与邮箱

**密码哈希不迁移**——这是正确决定，保持现状。`backend/scripts/migrate_users_to_lako.py:131-134` 的做法是：写完映射后把论坛 `password_hash` 置为随机值使其永久失效，由 Lako 侧发 set-password 邀请。`claim` 流程同理。

理由：兼容导入 argon2 哈希会把论坛的密码库变成 Lako 的攻击面，还要求两套哈希参数永久保持一致，得不偿失。代价是**全员需要改一次密码**。

**⚠️ 最大的运营风险，上线前必须先量化**：论坛内测期大量使用假邮箱（`ALLOWED_EMAIL_DOMAINS` 在生产只打告警，见 `backend/src/samryetha/routers/auth.py:428-431`），而迁移脚本对没有 `recovery_email` 的用户填 `<username>@migrated.invalid`（RFC 2606，**永不投递**）。

结果是：**这批用户既收不到邀请邮件，也无法自助重置密码**，只能靠管理员代设。上线前必须先统计 `recovery_email` 覆盖率，据此评估需要多少人工作业。

### 5.4 现有的不一致，顺手修

- **role 同步只升不降**：`backend/src/samryetha/oidc.py:378-380` 只在用户属于 admin 组时把自己改成 admin，**从不降权**。被移出 `samryetha-admins` 的用户会永久保留管理员权限。阶段二从 groups 派生 role 时必须改成双向同步。
- **moderator 的 admin 语义三处不一致**：i18n 后端只认 `role == "admin"`（`i18n/src/i18n_svc/deps.py:26-27`），而翻译站前端把 moderator 也当 admin（`i18n/site/src/App.tsx:11-13`）。统一为一个规则，例如：

  ```
  is_admin     := "samryetha-admins" in groups
  is_moderator := is_admin || "samryetha-moderators" in groups
  ```

---

## 6. 风险与回滚

### 6.1 风险清单

| 风险 | 说明 | 对策 |
|---|---|---|
| **Lako 宕 ⇒ 全站宕** | 在线校验的固有代价 | stale 缓存（≤300s）+ 写操作 fail-closed；文档显式声明"这是接受的代价" |
| **900 秒 token 陷阱** | 若误用 `/oauth/userinfo` 做会话校验，15 分钟后必然 401 且无法续期 | 文档显式禁止；会话校验只能走 4.2(B) 的新端点 |
| **本地 `COOKIE_DOMAIN` 无效** | 浏览器对 `localhost` 的 Domain 属性处理特殊，`.samryetha.com` 在本地不起作用 | 本地留空；跨子域测试另开 hosts 映射，不要污染默认参数 |
| **四条路径漏 domain** | `_issue_session_cookie` 被 QR/claim/claim-new/emergency 调用，漏 `domain=` 会导致跨子域静默失效 | 阶段一顺手修（3.1） |
| **推 dev/main 五分钟自动上生产** | 阶段二是行为切换，靠"重新部署旧 commit"回滚不安全（期间可能已写数据） | 用 `AUTH_MODE` 开关，见 6.2 |
| **数据迁移不可逆** | `sessions` 表删了就回不去了 | 删表放到最后一个部署 |

### 6.2 回滚

**阶段一**（无 schema 变更、无数据变更）：revert 那个 PR 重新部署即可（约 5 分钟）。建议拆成三个独立可回滚的 PR：① `safe_return_to` 白名单 + `_issue_session_cookie` 补 domain（纯后端）；② 前端登录页薄壳化 + 入口收敛；③ 翻译站 sign-in/out。

**阶段二**（必须用开关，分四次部署）：

| 部署 | 内容 | 回滚方式 |
|---|---|---|
| 1 | 加 `AUTH_MODE=local\|lako`（**默认 `local`，行为完全不变**）；Lako 端点/schema 全部建好但不用；两套 resolver 并存 | 无风险，可直接 revert |
| 2 | 跑 `migrate_users_to_lako.py --dry-run` → 正式跑 → 人工核对报告 | 数据层可回退（删 `oidc_identities` 映射行） |
| 3 | 切 `AUTH_MODE=lako` | **改 env 秒级回滚**，无需重新部署；此时 `sessions` 表还在，旧路径可无缝恢复 |
| 4 | 观察数天后：删除 `sessions` 表、旧 resolver、密码/QR/claim 代码 | **此步之后无法快速回滚**，务必确认无误再执行 |

关键约束：**在部署 4 之前，绝对不要 drop `sessions` 表。**

---

## 7. 验证与灰度

### 7.1 本地拓扑

| 服务 | 端口 |
|---|---|
| 论坛前端 / 后端 | 3000 / 3001 |
| i18n 服务 | 3002 |
| Lako web / api | 4010 / 8000 |
| 翻译站 | 5200 |

Lako 本地用 SQLite 即可（`lako/api/app/common/config.py:13` 默认就是 `sqlite+aiosqlite`），不必起 PostgreSQL。但**生产的 Postgres 差异本地复现不了**，灰度环境要用 docker-compose 验一次。

### 7.2 阶段二验证清单

1. 校验端点：正确 token → 200 且 `sub` 是 UUID；错误 token → 401 且 code 正确
2. 过期/已撤销会话 → 401 `SESSION_EXPIRED` / `SESSION_REVOKED`
3. Lako 侧登出 → 论坛与 i18n 的下一次请求（≤ 缓存 TTL）双双失效
4. **封禁一致性**：Lako 里禁用某用户 → ≤ TTL 内论坛发帖被拒、i18n 提交被拒
5. **Lako 宕机演练**：停掉 Lako api → 确认 stale 生效（最近活跃用户仍可读）、**写操作被拒**（fail-closed）；恢复后自动回转
6. `AUTH_MODE` 在 `local` ↔ `lako` 之间切换，确认秒级回滚有效且 `sessions` 数据未损
7. 三种角色（admin / moderator / student）走一遍，确认论坛 `is_admin`、i18n `require_admin`、翻译站 admin tab 三者结论一致
8. **删表前**：`grep -rn "sessions" backend/src i18n/src` 确认没有任何代码再读 `sessions` 表

### 7.3 生产灰度

- 阶段一按 3 个 PR 分别上，每个独立观察
- 阶段二严格按 6.2 的四次部署，部署 3 → 4 之间观察 ≥ 1 天
- 上线前量化三个数：`recovery_email` 覆盖率、迁移脚本的 `manual review` 数量、`oidc_identities` 已映射用户占比——这三个数直接决定"多少用户会被挡在门外"
- 考虑在阶段二期间临时暂停 `main` 的自动部署，改为人工触发，避免误 push 直接上线

---

## 附录：与 `sso-auth-design.md` 的差异

仓库里已有一份 `.claude/plans/sso-auth-design.md`（统一单点登录设计）。本设计与它有两处**明确冲突**，在此说明，避免后来人困惑：

**1. `明确不采用` 的第一条被推翻了**

旧文档第 99 行写：

> 不使用跨子域共享的 `.samryetha.com` 登录 Cookie。

理由是"避免一个子域被攻破后读取全站 SSO Cookie"——这是正确的安全实践。

但"论坛完全不参与认证"最省事的形态恰恰需要父域 cookie（或等价的"各服务在线校验 Lako 会话"）。本设计选择的是**在线校验**路线，代价是引入了一个共享的服务 token 和 Lako 的可用性依赖。**如果坚持旧文档的立场，就必须接受"论坛保留自己的会话"——而这正是本次要消除的东西。** 二者只能选一，最终由是否愿意承担父域 cookie / 在线校验的风险决定。

**2. "不要手写 IdP"这条已经过时**

旧文档推荐用 Authentik / Keycloak，并写明"不要在论坛项目内手写密码、OIDC 签名、密钥轮换和 MFA"。

但仓库现在已经有了 `lako/`——一个自研的 OIDC IdP，且 PR #43 已经按它落地了迁移脚本、认领页、扫码登录、密码退役开关。**事实上已经选了自研路线**，旧文档这条不再适用，仅作历史记录保留。

**仍然有效、本设计沿用的部分**：`sub` 作为不可变身份主键（`oidc_identities` 的 `UNIQUE(issuer, subject)` 正是这个）；不把未验证的 `X-User` / `X-Forwarded-*` 当身份；不把 access token 放 localStorage；服务端 `require_active_user` + role 严格校验而不只靠前端隐藏导航。
