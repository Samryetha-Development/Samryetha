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

## 回滚

- 映射写在论坛库 `oidc_identities`，删行即解绑（用户恢复密码登录）。
- Lako 侧账号保留无妨（不再被引用）。
- `migration-state.json` 与论坛库备份一并保存。
