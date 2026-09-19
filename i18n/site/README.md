# Samryetha Translation Site

独立构建的 Vite + React 单页应用，让登录用户在翻译站提交翻译建议、让管理员审核发布。
全部数据走**独立 i18n 服务**（`:3002`，见 `../README.md`），不是主站 forum 后端。

## 页面

| Tab | 登录要求 | 说明 |
|-----|---------|------|
| Source Strings | 无 | 浏览全部源字符串，按语言查看已审核译文 |
| Submit Translation | 是（active user） | 对任意源字符串提交翻译建议 |
| My Submissions | 是 | 追踪自己的提交状态（pending / approved / rejected） |
| Admin Review | 是（admin / moderator） | 批准 / 驳回待审提交并填写驳回原因 |

## 开发

```bash
cd i18n/site
npm install
# 先启动 i18n 服务（i18n/ 下：uv run python -m i18n_svc.main）
npm run dev          # :5200，代理 /api → http://localhost:3002（i18n 服务）
```

指向其他后端：

```bash
VITE_API_TARGET=http://my-i18n-server:3002 npm run dev
```

类型检查：`npm run typecheck`

## 生产构建

```bash
npm run build        # 产物在 i18n/site/dist/
```

`dist/` 是标准 SPA（`index.html` + assets），非 API 路径全部回退 `index.html`。

### 推荐部署：由 i18n 服务托管

给 i18n 服务设置 `I18N_SITE_DIR=$PWD/i18n/site/dist`（见 `../.env.example`），
本服务会把 `/assets/*` 当静态文件、其余非 API 路径 SPA fallback 到 `index.html`。
翻译站与 API 完全同源，浏览器自带会话 cookie，`deploy.sh` 已按此方式接线
（`I18N_DOMAIN=i18n.samryetha.com` → nginx → `:3002`）。

### 其他方式

- **独立静态服务器**：`npx serve dist -p 5200` 等；此时 API 跨域，需让 i18n 服务
  `I18N_SITE_ORIGIN` 包含该站点地址，并确认会话 cookie 可共享（子域 + `COOKIE_DOMAIN`）。

## API（i18n 服务，均为 `/api/...` 前缀）

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| `GET` | `/api/catalog/{locale}` | 公开 | 该 locale 全部条目 |
| `GET` | `/api/catalog/en` | 公开 | 英文源字符串（提交/对照用） |
| `GET` | `/api/submissions` | 登录 | 提交列表（普通用户见自己的，admin/moderator 见全部） |
| `POST` | `/api/submissions` | active user | 提交翻译 |
| `POST` | `/api/submissions/{id}/review` | admin | `{action: "approve"|"reject", note?}`；approve 时同事务 upsert catalog |

## 设计

遵循 `frontend/design.md`：近白底、`--line` 1px 细线、浊雾蓝强调（`--accent-fill: #3d7dbf`）、胶囊按钮、系统字体栈。
无外部 UI 库，样式集中在 `src/styles.css`，含 `prefers-reduced-motion` 与响应式断点分支。