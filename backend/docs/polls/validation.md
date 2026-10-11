# 验证记录

基于 fork `hwc2102/Samryetha` 的 `dev`，起点 `a736df5`。2026-10-09 复核时与上游 `dev` 一致。

- 后端完整测试：371 通过（包括 19 个新增投票用例）。
- Python 编译、Ruff、basedpyright strict：通过，无类型错误或警告。
- 前端 TypeScript 检查、客户端和 SSR 构建：通过。
- SSR：17 个路由/语言场景通过；登录跳转和文件页面 StrictMode 检查通过。
- 投票 UI StrictMode 检查：配置刷新清除失效选择、失败后保留选择并重试、重复点击只提交一次、刷新帖子锁定状态及移除投票均通过。在 `frontend/` 运行 `node scripts/polls-smoke.cjs` 可复现。
- 实际浏览器：创建投票、草稿保存和重载恢复、发布、多选提交、改票、刷新页面保留选择、已投票后的配置锁定、编辑正文保留统计、帖子锁定/解锁、390px 手机投票卡片、访客登录入口、单选互斥均通过，无页面脚本错误。
- 页面截图见本目录；API、schema、权限与升级说明见上级 `polls.md`。

额外运行的 `audit_architecture.py` 仍报告现有 `moderation/visibility.py:is_reported` 的边界问题（SQL 位于非 repository 文件）。该文件未被本次改动修改，此检查不属于当前 PR CI 工作流，未将其计入通过项。

构建保留现有的大分块提示；后端测试保留 Starlette/httpx 的弃用提示。以上均未导致检查失败。
