# 文件服务审查修复交接

日期：2026-10-25  
基线：`f079709f3d3315b4ecfbaaf4cb58bd4dcade0abe`（`dev` / `origin/dev`）  
当前状态：10 项审查问题 **全部修复并完成逐项独立复核**。原始 Task 6（原生预览）与 Task 7（Office 预览代理超时）已按本文件第 3、4 节完成，全量验收见第 5 节。所有产品改动均未提交。

## 1. 当前工作区注意事项

- 不要丢弃以下已修改文件，它们包含已完成的修复和测试：
  - `backend/src/samryetha/adapters/storage.py`
  - `backend/src/samryetha/attachments/{repository.py,router.py}`
  - `backend/src/samryetha/files/{models.py,repository.py,router.py,service.py}`
  - `backend/tests/test_files{,_attachment_promote,_audit,_document_preview,_media_preview,_public}.py`
  - `backend/docs/openapi.json`
  - `.github/workflows/pr-checks.yml`（新增两个文件冒烟步骤）
  - `docs/file-service/00-plan.md`
  - `frontend/server.mjs`（改用共享的超时判定）与新文件 `frontend/api-proxy.mjs`
  - `frontend/src/{file-detail-page.tsx,files-page.tsx,root-app.tsx}`
  - `frontend/src/lib/api.ts`（预览票据可中止）与 `frontend/src/lib/locales/{en,zh-CN}.ts`（`file.previewFailed`）
  - `frontend/src/lib/generated/openapi.ts`
  - `frontend/scripts/{file-ui-usability-smoke.cjs,files-strictmode-smoke.cjs}`
  - 新文件 `frontend/scripts/file-preview-smoke.cjs`（7 个用例，含原权威下载计数）
  - 新文件 `frontend/scripts/api-proxy-smoke.mjs`
- `backend/uv.lock` 的镜像源噪音已用 `git restore --source=HEAD --worktree -- backend/uv.lock` 恢复，当前未出现在 `git status` 中。
  - 全局镜像配置在 `~/.config/uv/uv.toml`，保留它可以解决依赖下载超时。
  - 后续 `uv run` 可能再次改写锁文件；最终验证后需再次检查 `git diff -- backend/uv.lock`。
- `PR_PUBLISH_TIME.md` 是用户原有未跟踪文件，与本次工作无关，不要修改或纳入提交。
- `.superpowers/sdd/hulk-fire-groot/` 是本次过程记录，包含任务 brief、报告、差异包和 `progress.md`，不是产品交付内容。

## 2. 已完成并通过独立复核的内容

### 2.1 长文件名扩展名与 80 字符边界

- `sanitize_filename()` 在截断 basename 时保留 suffix，且 suffix 本身达到/超过 80 字符也不会触发负数切片或突破总长度。
- 修复长 PDF/Office 文件的 MIME、下载文件名和转换预览。
- 测试：`test_files_media_preview.py`、`test_files_document_preview.py`。
- 第一轮独立复核发现超长后缀边界，已补测试、修复并复审通过。

### 2.2 文件/附件流式响应前释放数据库连接

- `files/router.py` 与 `attachments/router.py` 在短 `request_conn()` 中完成会话、资源和权限检查，只把响应快照带出上下文。
- `FileResponse` 发送期间不再持有数据库连接。
- 签名、权限、MIME、Content-Disposition 和 Range 行为保持不变。
- 回归测试在消费响应体之前断言 `engine.pool.checkedout() == 0`，并实际校验响应字节和 206 行为。

### 2.3 公开直链收藏

- 仅个人收藏查询在既有可见性之外额外允许 `is_public=1`。
- `visibility=private` 的公开直链仍不会进入匿名总列表。
- 非 owner 收藏后可在 `/api/files/favorites` 看到资料。

### 2.4 权威下载计数

- `FileDownloadTicket` 新增必填 `downloadCount`，普通下载在记录后返回服务端权威值，预览票据只返回当前值且不记下载。
- 详情页不再每次点击都本地 `+1`，改用 `ticket.downloadCount`。
- `backend/docs/openapi.json` 与 `frontend/src/lib/generated/openapi.ts` 已同步。
- `frontend/scripts/file-preview-smoke.cjs` 已覆盖连续两次下载仍显示服务端去重计数 1。

### 2.5 软删除对象回收

- 文件回收器的引用集合 = 全部附件对象键 + 未软删资料对象键。
- 附件手动删除护栏和孤儿回收也只把未软删资料视为认领方。
- 普通资料软删后可回收；转入资料在源附件存活时仍受保护；源附件合法删除/回收后对象可释放。
- 软删资料行和 object key 唯一约束不变，软删后重复转入仍为 409。
- `docs/file-service/00-plan.md` 已同步删除与回收语义。

### 2.6 跨账号状态隔离

- `FilesPage` 和 `FileDetailPage` 在 `root-app.tsx` 中按用户身份设置 React key。
- 身份变化会关闭并清空编辑、上传和移动弹窗。
- 编辑详情请求使用请求代数失效判断；账号切换前的 pending 响应及其 `finally` 不能重开旧弹窗或清除新请求 loading。
- 已覆盖“编辑器已打开”和“A 请求 pending → 切到 B → A 迟到返回”两种场景。

## 3. 已完成：原生预览修复

主要文件：`frontend/src/file-detail-page.tsx`。保留既有 `ticket.downloadCount` 行为，全部按 TDD 完成（先写 `frontend/scripts/file-preview-smoke.cjs` 扩展用例并看到 RED，再实现 GREEN）：

1. **无关状态更新不得重建预览**
   - 预览 effect 只依赖资源 ID、扩展名、当前身份和显式 retry attempt，不再依赖整个 `detail`。
   - 同账号的收藏、评分、下载只更新展示字段，不会清空预览、重置媒体播放位置或重复申请票据（冒烟用例断言三种操作后票据请求次数始终为 1）。

2. **显式失败与重试**
   - 预览改为状态机：`idle / loading / url / text / failed`；文本非 2xx、票据请求失败、PDF 非 2xx/非 `application/pdf`、图片 `onerror` 都进入 `failed`。
   - 新增中英 `file.previewFailed` 文案，失败态显示说明 + `common.retry`；重试会重新申请票据。
   - 媒体播放器解码失败继续使用 `file.mediaUnsupported`；Office 预览状态机（`document-preview.tsx`）未改动。
   - PDF 先获取签名 URL、校验 HTTP 成功与 `application/pdf` 后再创建 Blob URL 并挂载 iframe；替换/卸载时撤销 Blob URL。

3. **文本限量流式读取**
   - 新增 `readTextPrefix()`：`ReadableStream` reader + `TextDecoder`（`stream: true`）分块累积，达到 4000 字符后立即 `reader.cancel()`；跨块 UTF-8 字符正确保留。没有流式响应体时回退到整段读取再截断。
   - effect 使用 `AbortController`，卸载/依赖变化中止在途请求，迟到响应不得更新状态（`api.files.preview` 新增可选 `signal` 参数）。

4. **测试与 CI**
   - `frontend/scripts/file-preview-smoke.cjs` 已扩展为 7 个用例，保留并通过原有下载计数用例；覆盖文本非 2xx 可重试、流式上限与提前取消、跨块 UTF-8、图片错误重试、非 PDF 不进 iframe、PDF Blob URL 卸载撤销、无关更新不重复申请票据、卸载中止在途请求。
   - 已加入 `.github/workflows/pr-checks.yml` 的 `File preview smoke` 步骤。

## 4. 已完成：Office 预览代理超时

- 新增无副作用模块 `frontend/api-proxy.mjs`：
  - `DEFAULT_API_TIMEOUT_MS = 30_000`，`DOCUMENT_PREVIEW_TIMEOUT_MS = 310_000`（超过后端 `PREVIEW_TIMEOUT_SECONDS` 的 300 秒上限）。
  - `apiTimeoutMs(url)` 用 `URL` 只取 pathname，精确匹配 `/api/files/resources/{数字id}/document-preview`（查询串不影响判定）；相似路径、非数字 id、大小写差异与编码变体都不会误匹配。
- `frontend/server.mjs` 仅把固定 `upstream.setTimeout(30_000)` 换成 `apiTimeoutMs(req.originalUrl)`，保留既有 502/504 处理。
- 新增 `frontend/scripts/api-proxy-smoke.mjs`（纯函数判定 + `server.mjs` 确实使用共享判定的接线断言），已接入 `.github/workflows/pr-checks.yml` 的 `API proxy timeout smoke` 步骤。

## 5. 验证证据

- Task 1 完整后端：`558 passed, 2 skipped`。
- Task 2 完整后端：`560 passed, 2 skipped`。
- Task 3 完整后端：`561 passed, 2 skipped`；前端 smoke、typecheck、build 通过。
- Task 4 完整后端：`564 passed, 2 skipped`。
- Task 5 两项身份相关冒烟、`pnpm typecheck`、`pnpm build` 通过。
- Task 6 + Task 7 完成后统一验收（2026-10-25）：
  - 完整后端：`564 passed, 2 skipped`（macOS；跳过的 2 项需要真实 LibreOffice/Linux 沙箱）。
  - `node scripts/file-preview-smoke.cjs`：7 个用例全部通过（含原有下载计数用例）。
  - `node scripts/api-proxy-smoke.mjs`、`node scripts/document-preview-smoke.cjs`、
    `node scripts/file-ui-usability-smoke.cjs`、`node scripts/files-strictmode-smoke.cjs` 全部通过。
  - `node --check server.mjs`、`pnpm typecheck`、`pnpm build`（含 SSR 冒烟 22 条路由）通过。
  - `git diff --check` 通过；`backend/uv.lock` 已恢复，无镜像源地址噪音；`PR_PUBLISH_TIME.md` 未被触碰。
- 既有警告：Starlette/httpx 弃用警告、前端大 chunk 警告；不是本次引入的失败。
- `pnpm check:api` 的生成步骤已成功；其 `git diff --exit-code` 在未提交契约新增时按设计返回 1。契约提交后才能按原语义返回 0。
- Task 1–7 均已按交接要求实现；最终全分支差异审查已完成。

## 6. 验收命令（可重复执行）

```bash
# 新增/修改的原生预览与代理冒烟
cd frontend
node scripts/file-preview-smoke.cjs
node scripts/document-preview-smoke.cjs
node scripts/file-ui-usability-smoke.cjs
node scripts/files-strictmode-smoke.cjs
node scripts/api-proxy-smoke.mjs
node --check server.mjs
pnpm typecheck
pnpm build

# 完整后端回归
cd ../backend
uv run pytest

# 范围与格式检查
cd ..
git diff --check
git status --short
```

最后逐项核对 10 个原始问题，检查 `backend/uv.lock` 没有镜像源地址噪音、`PR_PUBLISH_TIME.md` 未被触碰，并做一次覆盖全部未提交差异的最终代码审查。
