# 同步 main 后自动递增版本

`Patch version after main sync` 在同仓库的 `dev → main` PR **合并后**创建独立的版本 PR。
只创建同步 PR、关闭但未合并、fork PR、普通功能 PR 和版本 PR 自身均不会再次递增版本。
现有的同步阈值和人工评审同步 PR 的流程保持原样。

例如当前版本为 `0.2.1`，同步 PR 合入 main 后会创建 `chore: bump version to 0.2.2`，
作者为仓库现有 `SYNC_PR_TOKEN` 的持有人，目标分支为 main，版本 PR 通过完整检查后自动 squash 合并。
末位不按十进制进位到次版本：`0.2.9 → 0.2.10`。

版本 PR 同步修改这三个文件，且只能修改对应的版本字段：

- `frontend/package.json`（页面版本号的来源）
- `backend/src/samryetha/__init__.py`（运行时 API 和 Python 包版本号）
- `backend/docs/openapi.json`（生成文档中的 API 版本号）

当前 Python 包使用动态版本号，`backend/uv.lock` 不记录本项目的版本；pnpm 的锁文件也不记录
根包版本，所以无需修改锁文件。后端检查会重新生成 OpenAPI 并验证没有漂移，前端仍检查 API 类型。

## 检查和自动合并

版本流程按 2026-10-12 的授权使用仓库已有的 `SYNC_PR_TOKEN` 创建版本 PR，无需新增 Secret。
当前仓库禁止内置令牌创建 PR，且执行账号没有管理员权限修改该开关；实跑记录：
[创建 PR 被策略拒绝](https://github.com/Samryetha-Development/Samryetha/actions/runs/38243020772)。
PAT 用于查询自身账号、创建版本 PR 和启动检查；推送版本提交、更新 PR 正文和合并使用
内置 `GITHUB_TOKEN`。合并时再次查询 PAT 的持有人并校验 PR 作者，不硬编码个人账号。
原 dev → main 同步流程的 **GITHUB_TOKEN-only / 机器人作者** 策略保持不变。
该原流程创建同步 PR 仍需要管理员允许 Actions 创建 PR；本次切换 PAT 仅适用于版本 PR。

准备步骤显式 dispatch `PR checks`，在**版本分支的实际提交**上运行原有后端、前端检查以及
自动化脚本测试，重试时也会重新启动检查。PAT 创建 PR 时还可能触发普通 PR 检查；合并流程
只接受显式 dispatch 的成功结果。GitHub 对内置机器人令牌创建的 PR 所触发的普通检查可能
要求人工批准，因此不依赖该事件完成自动链路。
dispatch 也使用 PAT：实跑时内置令牌启动的
[检查已成功](https://github.com/Samryetha-Development/Samryetha/actions/runs/38244097048)，
但没有产生后续合并所需的 `workflow_run`。使用 PAT 启动检查，避免这段事件链被防递归规则抑制。
参见 [GitHub 的工作流触发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。

检查成功后的 `workflow_run` 才会执行合并步骤。它从受信任的 main 读取脚本，不执行版本分支代码；
再次核对来源同步 PR、令牌持有人、PR 标记、文件内容和检查提交 SHA，然后调用 GitHub 合并 API，
并传入已检查的 SHA。检查失败、分支被修改、版本不一致或存在额外文件改动都会阻止合并。

不依赖仓库设置中的 **Allow auto-merge**：Action 在检查完成后直接请求合并，仍遵守分支保护
和评审要求，不使用管理员绕过。以后若增加必需评审或其他检查，未满足时版本 PR 会保持打开，
合并 Action 会明确失败。机器人令牌合并后的 push 不保证触发新的工作流；完整验证已在合并前完成。

## 重试和补跑

Actions → `Patch version after main sync` → Run workflow，选择 **main**，填写已合并的
同步 PR 编号。`dry_run` 默认为 true，只显示预期版本，不提交、不建 PR、不启动检查。
确认需要补跑时设为 false。部署本流程后也可以用此入口为已有同步 PR 补上版本更新。

每个来源同步 PR 使用唯一分支 `codex/version-bump-pr-<编号>`，不会重复创建版本 PR：

- 已合并的版本 PR：跳过，不再次加一。
- 仍打开的版本 PR：复用并重新启动检查；main 已前进时，用带旧 SHA 校验的推送刷新版本提交。
- 人工关闭的版本 PR：明确失败，不擅自重开。需要继续时先手动重新打开该版本 PR，再补跑。
- 检查期间 main 的版本字段发生变化：拒绝合并，补跑准备步骤，更新版本并重跑全部检查。

自动入口依赖同步 PR 合并产生的 `pull_request.closed` 事件。若其他流程用 `GITHUB_TOKEN`
合并同步 PR，GitHub 会抑制该事件的后续工作流；那个流程必须显式 dispatch 本工作流并传入
`source_pr` 和 `dry_run: false`。当前 dev → main 同步 Action 只创建 PR，合并仍由人工执行。

## 本地验证

```sh
node --test .github/scripts/patch-version.test.mjs
node .github/scripts/dev-to-main-sync.test.mjs
```

测试使用临时 Git 仓库和模拟 API，覆盖递增、格式保持、版本漂移、重复运行、提交及 dispatch
中断恢复、main 更新后的刷新、检查失败/过期、恶意文件修改、来源限制和合并权限拒绝。
