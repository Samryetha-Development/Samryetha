# 认证统一到 Lako 的迁移设计

目标：**登录入口全部收敛到 Lako**——用户在论坛、翻译站遇到"登录"，看到的都是 Lako，不再看到论坛自己的登录页。

本文只描述设计与迁移路径，不含代码改动。配套的操作手册见 [`oauth-migration.md`](./oauth-migration.md)（论坛用户 → Lako 的数据迁移）。

> **一句话结论**：只做第 3 节（统一入口）+ 第 5 节（用户迁移）就够了。
> 把"会话也搬到 Lako"那一套（第 4 节）**明确放弃**——它是非标准做法，代价大、收益只是架构洁癖，用户感知不到。
> 完整论证留在第 2 节和第 4 节，供将来有人再提起时直接引用。

---

## 1. 现状

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
                    │  · 发一次性 code + ID token   │
                    └──────────────────────────────┘
```

关键位置：

| 事实 | 位置 |
|---|---|
| 论坛发会话 cookie | `backend/src/samryetha/security.py:22` (`SESSION_COOKIE = "samryetha_session"`) |
| 会话存在论坛自己的表 | `backend/src/samryetha/schema.py:284-296` |
| i18n 复制了 cookie 名 | `i18n/src/i18n_svc/security.py:20` |
| i18n 直接读论坛的库 | `i18n/src/i18n_svc/security.py:36-49`（`sessions JOIN users` 裸 SQL） |
| 翻译站没有会话，点登录跳论坛 | `i18n/site/src/App.tsx:15-17` |
| Lako 的 session 是 host-only | `lako/api/app/authentication/routes.py:38-67`（`set_cookie` 无 `domain=`） |

**问题出在入口，不在会话。** 点登录掉到论坛那张密码表单页，是入口没收敛；论坛持有会话这件事本身是标准做法（见第 2 节）。

---

## 2. 基准：标准 SSO 是怎么做的

在改任何东西之前先对齐基准，否则很容易把"标准"当成"要修的毛病"。

**核心原则：IdP 只负责回答"你是谁"，每个应用各自发自己的会话。**

```
用户点登录
   │
   ▼
App (forum.example.com)
   │  302 → IdP /authorize?client_id&redirect_uri&state&nonce&code_challenge
   ▼
IdP (auth.example.com)
   │  没会话？→ 显示登录页 → 验密码 → 在 IdP 域下发 IdP 会话 cookie
   │  302 → 回 App，带 ?code&state
   ▼
App 后端（不是浏览器）
   │  拿 code + code_verifier 去 /token 换 token
   │  验 ID token：签名(JWKS) + iss + aud + exp + nonce
   ▼
App 用 (issuer, sub) 找到本地用户
   │
   ▼
App 发自己的会话 cookie  ← ★ 关键就是这一步
   │
   ▼
之后每个请求只查 App 自己的会话，不再问 IdP
```

注意：**ID token 只活几分钟是正常的**，它只管"证明一次身份"。真正撑着用户登录状态的是应用自己的会话。

**为什么每个应用要自己发会话**（四条实打实的理由，不是偷懒）：

1. **安全边界**：IdP 的会话 cookie 在 `auth.example.com`。把它放开到 `.example.com` 让所有子域共享，等于**任何一个子域被 XSS 或子域接管，攻击者就拿到全站通行证**。GitHub / Google / Okta 全都不这么干。
2. **撤销要能各自生效**：封人、降权希望**立刻**生效。本地会话可以当场删；依赖 IdP 校验就得等缓存过期。
3. **应用有自己的状态**：论坛的封禁到期时间、i18n 的角色、帖子权限都不在 IdP 里，本地会话才好叠上去。
4. **IdP 挂了不能全站挂**：本地会话让应用能自己活；每请求问 IdP 的话，IdP 一挂全站 500。

代价是"全局登出"麻烦，得靠 `end_session` + backchannel logout 挨个通知——这是标准架构公认的痛点，不是谁做错了。

**跨顶级域更是没得选**：两个应用在 `a.com` 和 `b.net` 时 cookie 根本无法共享，只能是"各发各的会话 + 跳转流"。所以本地会话不是妥协方案，是唯一方案。

**结论：`lako/README.md` 也是这么写的**——接入方"换取 code、校验 ID token、**建立自己的应用会话**"。论坛现在这套就是教科书流程，不需要改。

---

## 3. 要做的：统一登录入口

**目标**：不管从哪个入口点登录，看到的都是 Lako；论坛那张登录页（密码 + 注册 + 扫码）消失。会话归属不变，因此完全可逆。

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
- 只接受**精确 origin 匹配**（scheme + host + port 完全相同），**禁止**通配符，**禁止**后缀匹配（`https://i18n.samryetha.com.evil.com` 必须被拒）
- 继续拒绝 `//host`（协议相对）、反斜杠、以及任何含控制字符（CR/LF）的值
- 不在名单内 → 回落 `/`
- 名单只从 env 读，不落库、不由请求参数决定

**`backend/src/samryetha/routers/auth.py:129`**

```python
response = RedirectResponse(settings.app_origin.rstrip("/") + transaction["return_to"], status_code=302)
```

这是直接字符串拼接。`safe_return_to` 一旦允许站外绝对 URL，**这行必须同步改**（区分"站内路径 → 拼 app_origin"和"站外白名单 origin → 原样跳"），否则会拼出 `https://samryetha.comhttps://i18n...` 这种畸形地址。

**`backend/src/samryetha/routers/auth.py:358-367` `_issue_session_cookie`**

这个 helper 漏了 `domain=settings.cookie_domain`，而另外两处写 cookie（`routers/auth.py:139-148`、`:450-459`）都有。它被 QR 兑换、claim、claim-new、emergency 四条路径调用——跨子域部署时这四条会发 host-only cookie，导致登录"看起来成功但跨子域不认"。顺手补上。

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

## 4. 放弃的：把会话也搬到 Lako

> **状态：已否决。** 本节保留完整论证，供将来有人重新提起时直接引用，不用重新推导。

### 4.1 为什么否决

标准做法是各应用自持会话（第 2 节）。要做到"论坛零会话"，只有两条路，两条都不划算：

**路 A：父域共享 cookie（`.samryetha.com`）**
省事，但正是第 2 节第 1 条说的反模式——任何子域被攻破就等于全站沦陷。仓库里已有的 `.claude/plans/sso-auth-design.md` 也在「明确不采用」第一条里写死了这条。

**路 B：每个请求在线问 Lako**

```
论坛收到请求 → 带上会话 token 调 Lako /validate → 拿到 sub/groups → 继续
```

没有父域 cookie 的问题，但引入三个新代价：

- **Lako 变成每一次请求的硬依赖**：它挂了，论坛和 i18n 一起 500。要靠 stale 缓存缓解，而缓存又反过来破坏"封禁即失效"这个论坛现有的不变量（`backend/src/samryetha/deps.py:61-63` 会自动解封过期封禁）
- **要新建一整套接口**：Lako 现在**没有** `cookie_domain`、没有会话校验端点、没有 `end_session`、会话只活 900 秒且没有 refresh grant（`lako/api/app/oauth/routes.py:189-260` 只支持 `authorization_code`）
- **换不到用户能感知的东西**：入口收敛之后，用户已经完全看不到论坛在管认证了。只有读代码的人知道区别

**算下来：路 B 的工程量是第 3 节的十几倍，产出是架构洁癖。**

### 4.2 如果将来真的要做，需要补什么

留档：Lako 侧要加 `cookie_domain`（现在 `set_authenticated_cookies` 不传 `domain=`）、一个服务间校验端点（不能用 `/oauth/userinfo`——它认的是 900 秒过期的 access token，接上会导致用户每 15 分钟被踢）、`end_session_endpoint`（论坛侧 `oidc.py:200-208` 的 `end_session_url()` 已经对接好了，Lako 一广播就自动生效）、以及 OAuth client 的 `post_logout_redirect_uri` 白名单（现在 `OAuthClient` 模型没这个字段）。

论坛侧要删 `sessions` 表、`security.py` 的会话原语、四条签发路径、密码/QR/claim 全部端点；i18n 侧要删掉它的复制版校验器和 SQLite-only 的 `AuthDatabase`，改成带 httpx 的 HTTP 客户端（**i18n 运行时依赖里现在没有 httpx**）。

**部署必须用 `AUTH_MODE=local|lako` 开关，且 `sessions` 表要留到最后一个部署再删。**

---

## 5. 用户迁移（必做，因为密码要下线）

入口收敛之后论坛不再提供密码登录，所以**所有用户必须先在 Lako 里有账号**。这一段是硬前提，不是可选项。

### 5.1 role 映射

| 论坛 `users.role` | Lako group | 状态 |
|---|---|---|
| `admin` | `samryetha-admins` | 已在 seed（`lako/api/app/authorization/service_seed.py:44-47`） |
| `student` | `samryetha-users` | 已在 seed |
| `moderator` | `samryetha-moderators` | **不存在，需要新增** |

需要改两处：
- `lako/api/app/authorization/service_seed.py:44` 的组名列表追加 `samryetha-moderators`
- `backend/scripts/migrate_users_to_lako.py:192` 现在只传 `admin = (role == "admin")` 一个布尔，需要把 moderator 也传过去，并在 Lako 侧 `_converge_flags` 里映射到对应 Role

### 5.2 status：论坛的"临时封禁"留在论坛

| 论坛 `users.status` | Lako |
|---|---|
| `active` | `ACTIVE` |
| `banned` / `deactivated` | 非 ACTIVE |
| `pending` | 需决定（论坛的注册审核状态，Lako 无对应概念） |

论坛有"带过期时间的临时封禁 + 到期自动解封"（`backend/src/samryetha/deps.py:61-63` 调 `moderation.lift_ban_if_expired`），Lako 的 `UserStatus` 没有过期概念。**保留在论坛侧做 overlay**，不要搬进 Lako。

### 5.3 密码与邮箱

**密码哈希不迁移**——`backend/scripts/migrate_users_to_lako.py:131-134` 的做法是：写完映射后把论坛 `password_hash` 置为随机值使其永久失效，由 Lako 侧发 set-password 邀请。

理由：兼容导入 argon2 哈希会把论坛的密码库变成 Lako 的攻击面，还要求两套哈希参数永久一致。代价是**全员需要改一次密码**。

**⚠️ 最大的运营风险，上线前必须先量化**：论坛内测期大量使用假邮箱（`ALLOWED_EMAIL_DOMAINS` 在生产只打告警，见 `backend/src/samryetha/routers/auth.py:428-431`），而迁移脚本对没有 `recovery_email` 的用户填 `<username>@migrated.invalid`（RFC 2606，**永不投递**）。

结果是：**这批用户既收不到邀请邮件，也无法自助重置密码**，只能靠管理员代设。上线前必须先统计 `recovery_email` 覆盖率，据此评估需要多少人工作业。

### 5.4 `oidc_identities` 不要删

`backend/src/samryetha/schema.py:298-309`（`UNIQUE(issuer, subject)`）是论坛的永久身份链接表（Lako `sub` → 论坛 `users.id`）。用户迁移和首次 OIDC 登录都靠它。

### 5.5 现有的不一致，顺手修

- **role 同步只升不降**：`backend/src/samryetha/oidc.py:378-380` 只在用户属于 admin 组时把自己改成 admin，**从不降权**。被移出 `samryetha-admins` 的用户会永久保留管理员权限。
- **moderator 的 admin 语义三处不一致**：i18n 后端只认 `role == "admin"`（`i18n/src/i18n_svc/deps.py:26-27`），而翻译站前端把 moderator 也当 admin（`i18n/site/src/App.tsx:11-13`）。统一为一个规则：

  ```
  is_admin     := "samryetha-admins" in groups
  is_moderator := is_admin || "samryetha-moderators" in groups
  ```

---

## 6. 风险与回滚

| 风险 | 说明 | 对策 |
|---|---|---|
| **本地 `COOKIE_DOMAIN` 无效** | 浏览器对 `localhost` 的 Domain 属性处理特殊，`.samryetha.com` 在本地不起作用 | 本地留空；跨子域测试另开 hosts 映射 |
| **四条路径漏 domain** | `_issue_session_cookie` 被 QR/claim/claim-new/emergency 调用，漏 `domain=` 会导致跨子域静默失效 | 第 3.1 节顺手修 |
| **用户没迁完就下线密码** | 入口收敛后论坛不再有密码登录，没迁到 Lako 的用户会直接进不来 | 迁移先跑完（含 `manual review` 清零），再切入口 |
| **假邮箱用户无法自救** | 见 5.3 | 上线前先量化 `recovery_email` 覆盖率 |
| **推 dev/main 五分钟自动部署** | main → samryetha.com（生产），dev → development.samryetha.com。两者已分叉（main 26 / dev 32） | 生产发布必须合进 `main`；只推 dev 不会上生产 |

**回滚**：本方案无 schema 变更、无数据变更，revert PR 重新部署即可（约 5 分钟）。建议拆成三个独立可回滚的 PR：① `safe_return_to` 白名单 + `_issue_session_cookie` 补 domain（纯后端）；② 前端登录页薄壳化 + 入口收敛；③ 翻译站 sign-in/out。

> 注意顺序：**先跑用户迁移，再合 PR ②**。PR ② 一旦上线，论坛的密码登录入口就没了。

---

## 7. 验证与灰度

### 7.1 本地拓扑

| 服务 | 端口 |
|---|---|
| 论坛前端 / 后端 | 3000 / 3001 |
| i18n 服务 | 3002 |
| Lako web / api | 4010 / 8000 |
| 翻译站 | 5200 |

接法：论坛 `backend/.env` 配 `OIDC_ISSUER=http://localhost:4010` + `OIDC_CLIENT_ID=samryetha` + `OIDC_REDIRECT_URI=http://localhost:3000/api/auth/callback`；Lako `lako/api/.env` 的 `ALLOWED_ORIGINS` 必须含 `http://localhost:3000`（它同时喂给 `lako/web/next.config.ts` 的 CSP `frame-ancestors`）。Lako 本地用 SQLite 即可，不需要 Postgres。

**端口坑**：Vite HMR 不能用 3002（和 i18n 服务撞，且 HMR 是纯 WebSocket 服务，普通 HTTP 请求会被它回 `426 Upgrade Required`，导致 SSR 预取 i18n 词条静默变空）；Lako web 默认 3000 会和论坛前端撞，用 `pnpm exec next dev -p 4010` 让开。

### 7.2 验证清单

1. 从论坛（3000）和翻译站（5200）分别点登录 → 都直达 Lako，签完各自回原位
2. 直接访问 `/login` → 不再出现论坛密码表单
3. 第 3.4 节五个 `returnTo` 安全用例全部回落 `/`
4. 迁移完成后，任一被迁移用户能用 Lako 密码登录论坛，落到自己的**原账号**（不是新建空号）
5. `COOKIE_DOMAIN` 设为跨子域值时，QR 兑换的 `Set-Cookie` 必须带 `Domain=`

### 7.3 生产灰度

- 三个 PR 依次上，每个独立观察
- 合 PR ② 之前确认迁移脚本的 `manual review` 清单已清零
- 上线前量化：`recovery_email` 覆盖率、`manual review` 数量、`oidc_identities` 已映射用户占比

---

## 附录：与 `sso-auth-design.md` 的差异

仓库里的 `.claude/plans/sso-auth-design.md`（统一单点登录设计）有两条已经过时或需要修正：

**1. 「不使用跨子域共享的 `.samryetha.com` 登录 Cookie」——本设计完全采纳这条。**

旧文档第 99 行写：

> 不使用跨子域共享的 `.samryetha.com` 登录 Cookie。

理由是"避免一个子域被攻破后读取全站 SSO Cookie"。**这个判断是对的**，本设计的第 4 节在此基础上进一步算出：既然不走共享 cookie，那"论坛零会话"的另一条路（每请求在线校验）代价更大而收益只是架构洁癖，**所以两条都不走，保留论坛自己的会话**。

**2. 「不要手写 IdP，用 Authentik / Keycloak」——这条已过时。**

旧文档推荐用成熟 IdP，并写明"不要在论坛项目内手写密码、OIDC 签名、密钥轮换和 MFA"。

但仓库现在已经有 `lako/`——一个自研的 OIDC IdP，且 PR #43 已经按它落地了迁移脚本、认领页、扫码登录、密码退役开关。**事实上已经选了自研路线**，旧文档这条不再适用，仅作历史记录保留。

**仍然有效、本设计沿用的部分**：`sub` 作为不可变身份主键（`oidc_identities` 的 `UNIQUE(issuer, subject)` 正是这个）；各应用自持会话（旧文档第 3 节第 6 步"创建现有 `sessions` 行"）；不把未验证的 `X-User` / `X-Forwarded-*` 当身份；不把 access token 放 localStorage；服务端 `require_active_user` + role 严格校验而不只靠前端隐藏导航。
