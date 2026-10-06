# OAuth 迁移操作手册（论坛用户 → Lako）

## 前提

- Lako 已部署并配置：`ADMIN_IMPORT_TOKEN`、SMTP、`samryetha` OAuth client（含生产回调地址）。
- 论坛 `OIDC_ISSUER` 指向 Lako（与映射写入的 `--issuer` **逐字符一致**）。
- 拿到论坛生产库**备份**后再动手。

## 步骤

### 1. 演练（dry-run，不写任何库）

```bash
cd backend
python scripts/migrate_users_to_lako.py \
  --lako-url https://auth.example.com \
  --token "$LAKO_ADMIN_TOKEN" \
  --issuer https://auth.example.com \
  --forum-db /path/to/app.db \
  --dry-run
```

看 `manual review needed` 列表：处理用户名不合规、邮箱被外部账号占用等个案。
共享 recovery 邮箱会自动回退到占位地址（报告里可见）。

### 2. 正式导入（先不发邀请）

```bash
python scripts/migrate_users_to_lako.py \
  --lako-url ... --token ... --issuer ... --forum-db ... 
```

- 每个用户：Lako 建号 → 写论坛 `oidc_identities` 映射 → 记 `migration-state.json`。
- 同命令可反复跑（幂等：已处理用户只做一致性复核，不会重复建号/重复映射）。
- 退出码 1 = 有需人工项（看报告），0 = 干净。

### 3. 发邀请（用户设 Lako 密码）

确认映射无误后加 `--send-invites` 再跑一遍：只给**真实邮箱**用户发 7 天有效的设密码邮件。
占位邮箱（`@migrated.invalid`）收不到也不会发——这些用户走第 4 步。

### 4. 查漏

- `manual review` 清单逐条处理（改名/换邮箱后重跑对应用户——删掉其 state 行再跑即可重走全流程）。
- 占位邮箱用户：让其先用论坛密码登录一次，在**设置页**把 recovery 邮箱改成真实邮箱（论坛侧已有流程），
  下次跑脚本加 `--send-invites` 即可收到邀请；或走认领页自助绑定。

### 5. 认领页（无需预埋的兜底）

未被迁移的用户首次 OAuth 登录会落到 `/claim?ticket=...`：
凭老用户名+密码绑定一次即可（5 次错票据作废）；纯新用户点“创建新账号”。

注册入口保留打开弹层时的路径、查询参数和片段。OIDC 回调与嵌入流的
`claimUrl` 会携带经过后端 `safe_return_to` 校验的 `returnTo` 查询参数；
认领页完成绑定或创建后返回该页面。认领页仅接受当前论坛源的目标，
缺失、无效或站外目标回落 `/`。此续跳不需要数据库迁移。

## 回滚

- 映射写在论坛库 `oidc_identities`，删行即解绑（用户恢复密码登录）。
- Lako 侧账号保留无妨（不再被引用）。
- `migration-state.json` 与论坛库备份一并保存。

## 附录：扫码登录（QR login）

PC 登录页“扫码登录”弹窗展示二维码（`POST /api/auth/qr/start` 返回 `qr_data_uri`），
手机扫码打开 `/qr/approve?t=<ticket_id>` 确认，PC 经 SSE（`GET /api/auth/qr/wait`）
收到批准后凭内存中的 secret 换会话（`POST /api/auth/qr/exchange`）。

威胁模型与对策：
- 票据 id 公开（进二维码/推送通道），但**换不到会话**——兑换必须 secret（只留 PC 内存，不进 URL/日志/推送）。
- 单次有效，用后即焚；2 分钟 TTL（`QR_LOGIN_TTL_MS`）；拒绝/过期即终态。
- 确认页展示请求方 IP/UA/时间，手机端必须显式点批准；拒绝同样单次。
- 批准者账号被删/封禁/停用时兑换失败；批准要登录态 + 限流。
- 手机未登录时先去登录，ticket 暂存 sessionStorage，登录后由 RootApp 带回批准页继续。

### 邮箱验证码二次确认（`qr_login_confirmation_codes`）

手机点一下批准就等于把账号交给 PC，所以**邮箱可投递的账号**还要证明自己控制邮箱：
`GET /api/auth/qr/info` 返回 `emailConfirmationRequired` / `emailHint`（掩码地址，
前端只拿到 `a***@example.com`），批准前先 `POST /api/auth/qr/confirm/request` 取一封
6 位码邮件，再 `POST /api/auth/qr/approve` 带上 `code`。

- **触发条件**：账号有非空邮箱、`email_verified_at` 非空、且地址不是本机生成的
  `@samryetha.local` 占位地址（`requires_email_confirmation`）。本地注册还没走过 Lako
  的账号邮箱就是占位地址，流程与改动前完全一致，不会被锁死——这是刻意的降级路径。
- 码只存 `hash_token(f"{user_id}:{code}")` 并绑定用户（不同用户的同一个 6 位码互不通用）；
  10 分钟 TTL、5 次尝试上限、单次使用；失败一律同一句错误，不区分"没发过"与"码不对"。
- 发起新码会**顺延票据有效期**（取 `QR_LOGIN_TTL_MS` 与邮件码 TTL 的较大者），否则 2 分钟的
  扫码窗口会让用户还没看完邮件就已经过期。
- 拒绝（`/qr/deny`）任何时候都不需要验证码——拦一个可疑登录不该反过来要求更多凭据。
- 缺 `code` 的批准返回 403 `EMAIL_CODE_REQUIRED`；码错误/过期/已用返回 400 通用文案。
- 尝试次数在**独立事务**里提交：请求事务会随 `ApiError` 一起回滚，写在请求连接上的计数
  会被静默丢弃，5 次上限就成了摆设。
