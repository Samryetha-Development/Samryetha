# dev -> main 自动同步 PR（积累若干 PR 后自动发起）

> 需求：每积累 **10 个**新的 PR 合入 `dev`，就自动发起一个从 `dev` 到 `main` 的 PR。
> 实现：GitHub Actions 工作流 `.github/workflows/dev-to-main-sync.yml` + 零依赖 Node 脚本
> `.github/scripts/dev-to-main-sync.mjs`（决策）与 `.github/scripts/dev-to-main-sync-report.mjs`（只报告）。
> 本文说明触发条件、计数口径、阈值调整、所需权限与 Secret、上线步骤、**已发生的线上故障记录**，
> 以及**仍未验证的部分**。

---

## 0. 线上故障记录（2026-10-10 首次真实运行）

功能合入 `dev`（merge `7ae3a6e`）后立刻产生了第一次真实运行，**暴露一处缺陷，已修复**。

| 运行 | 事件 | 结论 | 关键步骤 |
|---|---|---|---|
| run 38023246106 | `pull_request`（来自 fork 的 PR #103） | **failure** | `Report fork PR delegated to the push run` **失败** |
| run 38023246104 | `push`（dev, sha `7ae3a6e`） | **success** | 决策与自测步骤均成功 |

**根因不是脚本，而是 YAML 注释截断。** 原来的写法是未加引号的 YAML 纯量：

```yaml
run: echo "PR #${{ github.event.pull_request.number }} came from a fork; the push run on dev covers it."
```

YAML 规定 **空格 + `#` 是注释起点**，于是该纯量在解析时被截断成 `echo "PR`；bash 因引号不闭合
报错并以 **exit 2** 结束作业。失败日志原文：

```
##[group]Run echo "PR
echo "PR
shell: /usr/bin/bash -e {0}
##[endgroup]
/home/runner/work/_temp/ed1e01de-352c-4634-9119-fc273830536d.sh: line 1: unexpected EOF while looking for matching `"'
##[error]Process completed with exit code 2.
```

同一写法在另一处（`Report skipped non-merge close`）也埋着同样缺陷，只因该步骤被跳过才没暴露。

**修复**：把这些说明文字从 `run:` 里搬进脚本文件 `.github/scripts/dev-to-main-sync-report.mjs`，
workflow 中三条 `run:` 现在都只是 `node .github/scripts/...` 这种无特殊字符的命令，该类截断在结构上
不可能再发生；同时该步骤显式 `process.exitCode = 0`，并把结论写进 `::notice::` 与 Step Summary。

**为什么之前的静态检查没抓到**：actionlint 需要 `shellcheck` 才能检查 `run:` 里的 shell，而本机没有装
`shellcheck`（这一点原本就已在"未验证项"里如实标注为缺口）—— 它正好咬在这个缺口上。
现已补一条**不依赖 shellcheck** 的静态守卫：检查 workflow 中未加引号的 `run:` 纯量里是否出现 `" #"`，
并配一条"必须能抓住这次真实故障那一行"的对照用例。

**幂等语义未变**：fork PR 与"关闭未合并"两类事件仍然**只报告、不决策**（决策步骤的 `if` 条件保持不变），
因此放宽退出码不会让 `pull_request` 路径也去重复创建 PR。

---

## 1. 触发条件

同步 PR 合入 main 后的版本递增由独立的
[版本更新 Action](patch-version-action.md) 完成：创建末位加一的版本 PR，检查通过后自动合并。
本流程仍只负责创建 dev → main 同步 PR。

| 事件 | 条件 | 说明 |
|---|---|---|
| `pull_request` | `types: [closed]`、`branches: [dev]`、且 `merged == true` | 主触发：有 PR **合入 dev** |
| `push` | `branches: [dev]` | 补充触发：直接推送到 dev（绕过 PR）时也检查一次 |
| `workflow_dispatch` | 手动 | 演练/人工强制，带 `dry_run`、`threshold`、`force` 三个输入 |

`pull_request` 事件的 `branches` 过滤的是 **base 分支**，所以 `branches: [dev]` 正好等价于
"目标是 dev 的 PR"；再看 `github.event.pull_request.merged == true`，就精确等于"有 PR 合入 dev"。

**关键前提**：GitHub 只会用 **base 分支上存在的那份 workflow 文件** 来触发 `pull_request` 事件。
所以这个 workflow 必须先在 `dev` 上存在才可能生效。

**关于来自 fork 的 PR**：GitHub 官方文档明确写着「除 `GITHUB_TOKEN` 外，从 fork 仓库触发的工作流
**不会**拿到任何 secret」
（见 [Using secrets in GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
的 "Using secrets in a workflow" 一节）。本仓库的改动恰好经常来自 fork，
因此 `pull_request` 这条路径对 fork PR 只能拿到降权的 `GITHUB_TOKEN`，建 PR 会失败。
处理方式：把「关闭未合并」与「来自 fork」两类事件交给 `Report non-decision event` 步骤，
**只报告、不决策**；真正的评估交给同一次合并产生的 `push` 事件（它跑在基线仓库上下文里，
权限与 secret 都正常）。两个步骤的 `if` 条件互斥且穷尽：

| 事件 | `Report non-decision event` | `Run sync decision` |
|---|---|---|
| `pull_request`，已合并，同仓库 | 跳过 | **执行** |
| `pull_request`，已合并，来自 fork | **执行（只报告）** | 跳过 |
| `pull_request`，未合并 | **执行（只报告）** | 跳过 |
| `push` / `workflow_dispatch` | 跳过 | **执行** |

---

## 2. 计数口径（无状态）

每次触发都**现算**，不写任何计数器文件：

1. **锚点** `anchor` = 最近一次**已合并**的 `dev -> main` PR（`base=main`、`head.ref=dev`、`merged_at != null`）
   中 `merged_at` 最大的那个。
2. **计数** `count` = 在锚点之后（`merged_at > anchor.merged_at`）合入 `dev` 的 PR 数
   （`base=dev`、`merged_at != null`）。
3. **判定**：`count >= threshold` 且以下硬闸门全部通过时，创建 `head=dev`、`base=main` 的 PR。

### 为什么不用计数器文件

提交进仓库的计数器文件需要有人定期写它：会与 `dev` 上其他人的提交反复冲突，会在
rebase/force-push/回滚后漂移，还会在 workflow 自身失败时静默失准。API 现算没有任何持久状态，
重放同一个事件两次得到同样的结论，从根上避免了这类问题。

### 边界情况怎么处理

| 边界 | 处理 |
|---|---|
| 一次事件里 PR 数**跨过 10 的倍数**（例如一次性合入 23 个） | 仍然**只建一个** PR。锚点是"最近一次已合并的同步 PR"，所以该 PR 被合并后锚点前移，下个窗口从 0 重新计数，不会连发也不会漏计。 |
| **并发触发**（`pull_request` 与 `push` 对同一次合并同时触发） | 三重保护：① workflow 级 `concurrency: dev-to-main-sync`（`cancel-in-progress: false`）串行化；② **硬闸门**：已存在未合并的 `dev -> main` PR 时直接跳过；③ 锚点只认**已合并**的 PR，未合并的 PR 不影响计数。 |
| `dev` 与 `main` **已无差异** | `GET /compare/main...dev` 的 `ahead_by == 0` 时跳过（此时 PR API 只会返回 422）。 |
| 管理员**关闭但未合并**了同步 PR | 24 小时冷却期内不再重建（避免每次有 PR 合入 dev 就重复打扰）。可用 `SYNC_PR_COOLDOWN_HOURS` 调整，设为 `0` 关闭该行为。 |
| 仓库**从没有过**已合并的同步 PR（无锚点） | 退化为"最近 `SYNC_LOOKBACK_DAYS`（默认 90）天"的窗口，并在结果里把 `windowKind` 标为 `fallback-lookback` 以示区别。本仓库有锚点，这条分支只在本地 stub 测试中覆盖。 |
| GitHub Actions **无权创建 PR** | 脚本识别该 403 并在日志里直接给出要配的 Secret 名（`SYNC_PR_TOKEN`），见第 4 节。 |

---

## 3. 阈值怎么调

三种方式，优先级从高到低：

1. **手动运行时的输入**：Actions -> "Dev to main sync PR" -> Run workflow -> `threshold`。
2. **仓库变量**（推荐，改完即生效、无需改代码）：Settings -> Secrets and variables -> Actions ->
   Variables -> 新增 `SYNC_PR_THRESHOLD`（例如 `5`）。
3. **workflow 文件里的默认值**：`env.SYNC_THRESHOLD` 的 `|| '10'` 兜底值。

冷却期同理，可用仓库变量 `SYNC_PR_COOLDOWN_HOURS` 调整。

### 阈值选 10 合适吗（实测数据）

从 GitHub API 实测本仓库历史：`dev -> main` 同步 PR 全部由**人工**发起，共 9 次已合并
（#4 #30 #32 #53 #64 #66 #84 #89 #94）。相邻两次同步之间合入 `dev` 的 PR 数分别是：

```
3, 16, 0, 19, 6, 1, 12, 2, 3      （中位数 3，平均 6.9，最大 19）
```

结论：**阈值 10 可行，但偏高** —— 按历史数据只有 3 个窗口（16 / 19 / 12）能达到 10，
实际触发节奏会明显慢于现在的人工同步频率（现在大致是"几天一次"）。如果希望更接近现状，
建议用仓库变量把阈值设成 `5`。这是取舍问题，**默认仍按需求取 10**。

---

## 4. 权限与凭据（作者必须是 github-actions[bot]）

### 4.1 需要什么权限

- `contents: read` —— 读 PR 与分支比较。
- `pull-requests: write` —— **必需**，`GITHUB_TOKEN` 靠它创建 PR。

### 4.2 用哪个 token：只用内置 `GITHUB_TOKEN`

workflow 里的取值是：

```yaml
GH_TOKEN: ${{ github.token }}
```

**设计上不再回退到任何 PAT。** 管理员 2026-10-11 的明确要求是：这类同步 PR 的作者要稳定显示为
`github-actions[bot]`。用 PAT 建出来的 PR，作者会变成该 PAT 的持有者（升级前的 PR #105 就是
`SeaqUs` 这个用户而不是 bot）——这正是要消除的结果。因此这里既不做"PAT 优先"，也不做
"PAT 静默回退"：一旦凭据不足就**明确失败**，绝不悄悄换身份。

> 仓库里现存的 `SYNC_PR_TOKEN` Secret 现在**已不再被本工作流使用**。
> 若它没有别的用途，建议管理员删除以免日后误解（本工作流已不再引用它，并有测试守住这一点）。

### 4.3 前提条件：仓库/组织必须允许 Actions 创建 PR

`GITHUB_TOKEN` 建 PR 需要仓库（或组织）在
**Settings -> Actions -> General -> Workflow permissions** 里勾选
**"Allow GitHub Actions to create and approve pull requests"**。

若该开关未打开，工作流会**明确失败**并打印下面这段可操作提示（原文，实测输出）：

```
[dev-to-main-sync] ERROR: POST https://api.github.com/repos/<owner>/<repo>/pulls -> 403 {"message":"GitHub Actions is not permitted to create or approve pull requests."}
[dev-to-main-sync] HINT: this repository/organization does not allow GitHub Actions to create pull requests.
[dev-to-main-sync] HINT: Ask an admin to enable: Settings -> Actions -> General -> Workflow permissions ->
[dev-to-main-sync] HINT: "Allow GitHub Actions to create and approve pull requests"
[dev-to-main-sync] HINT: (an organization-level policy may also have to allow it), then re-run this workflow.
[dev-to-main-sync] HINT: no personal access token fallback is used on purpose: the sync PR must be authored by github-actions[bot].
```

另一类常见 403（token 缺少 `pull-requests:write`，报 `Resource not accessible by integration`）
会得到对症的提示，指向本 workflow 的 `permissions:` 块与仓库默认工作流权限。

### 4.4 副作用：`GITHUB_TOKEN` 建的 PR 不会自动跑其它工作流

这是 GitHub 的防递归硬规则，官方文档
（[GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)，
"When `GITHUB_TOKEN` triggers workflow runs" 一节）写明：

> 用仓库的 `GITHUB_TOKEN` 执行操作时，由它触发的事件**不会**创建新的工作流运行，但有两个例外：
> `workflow_dispatch` / `repository_dispatch` 总会创建；以及 **`opened` / `synchronize` / `reopened`
> 类型的 `pull_request` 事件会创建运行，但处于"需要批准"（approval-required）状态** ——
> PR 的合并框里会出现横幅，有写权限的用户点 **Approve workflows to run** 之后才会真正执行。

注意这不是简单的"完全不触发"：**它会创建运行，但停在等待批准**。对本功能的具体含义：

| | 用 PAT 建 PR（改动前，#105 的情况） | 用 `GITHUB_TOKEN` 建 PR（改动后） |
|---|---|---|
| PR 作者 | PAT 持有者（`SeaqUs`） | **`github-actions[bot]`** |
| `pull_request` 触发的 `pr-checks` | **自动运行** | 运行被创建，但**需要人工点"Approve workflows to run"** |
| 同一提交上的 `push` 触发检查 | 正常运行 | 正常运行（不受影响） |

**实测证据（2026-10-11，PR #105）**：`dev` 尖端 `5ed6360` 这个提交上同时存在两组同名检查——
一组来自 `push` 到 dev（run 38105234080，02:27:56Z 开始），一组来自建 PR 后的 `pull_request`
（run 38105247733，02:28:11Z 开始），**两组都是 success**。这直接支撑了第 4.5 节的判断。

### 4.5 与「main 上必须状态检查通过」共存的风险结论

**结论：预计满足，但"强制生效"这一环未验证。**

理由是必过状态检查是**按提交（PR 的 head commit）判定**的，而同步 PR 的 head 就是 `dev` 的最新提交——
该提交在合入 `dev` 时已经由 `push` 事件跑过一次 `pr-checks`，因此它**本来就带着同名的成功检查**。
实测证据见 4.4 节：`5ed6360` 上确实存在来自 `push` 的
`Backend (compile + lint + pytest)` 与 `Frontend (typecheck + build)` 成功检查。

**但有一处必须提醒**：开关切到 `GITHUB_TOKEN` 后，建 PR 时那个"等待批准"的 `pull_request` 运行
**可能**在同一个提交上追加一组同名、状态为排队/等待的新检查。若 GitHub 按"同名取最新"判定，
必过检查会显示为 pending，直到有人点 Approve。**这一点无法在本地或只读探测中确证。**

**验证方法（改动合并后、下一次自动同步 PR 出现时执行）**：

1. 取该 PR 的 head sha，看提交上的检查：
   `GET /repos/Samryetha-Development/Samryetha/commits/<sha>/check-runs`
   —— 观察是否出现 `status: queued`/额外一组同名检查。
2. 打开该 PR 的页面，看合并框里是否出现 "Approve workflows to run" 横幅。
3. 在 `main` 上开启必过检查规则后，看该 PR 是否直接显示为可合并（绿）还是停在 pending。

若结果是"停在 pending"，处理办法有二：要么由有写权限的人点一次 Approve（多一步人工），
要么**不要**把 `pr-checks` 设成 `main` 的必过检查、改为依赖 `dev` 上的 `push` 检查作为防线。

> 附：若既想要 bot 作者、又想让 PR 触发的工作流**无需批准**即自动运行，官方文档给出的办法是改用
> **GitHub App installation access token**（作者会显示为该 App 的 `<app>[bot]`）。本 PR 未实现该方案，
> 因为管理员明确要求的是 `github-actions[bot]`；此处仅作为备选记录。

---

## 5. 管理员上线步骤

1. **合并本 PR 到 `dev`**（这是前提：workflow 必须先在 `dev` 上存在才会被触发）。
2. **确认开关**：Settings -> Actions -> General -> Workflow permissions ->
   勾选 **"Allow GitHub Actions to create and approve pull requests"**。
   没勾的话，达到阈值时运行会失败并打印 4.3 节那段提示（不会有人替你静默换身份）。
3. **（可选）删除 `SYNC_PR_TOKEN`**：本工作流已不再使用它。
4. **先演练**：Actions -> "Dev to main sync PR" -> Run workflow -> 保持 `dry_run = true` -> Run。
   看运行日志与 Step Summary 里的 `decision` / `count` / `threshold`。演练**不会**创建任何 PR。
5. **（可选）调阈值**：按第 3 节设置仓库变量 `SYNC_PR_THRESHOLD`。
6. **正式启用**：`dry_run = false` 的手动运行可以直接强制发起一次同步；
   之后日常就由"PR 合入 dev"自动驱动，无需人工干预。
7. **观察**：每次运行都会在 Step Summary 里写明 `decision`（`created` / `would-create` /
   `skip`）、跳过原因、计数与阈值，方便判断是"还没到阈值"还是"被闸门挡住了"；
   自动建出的 PR 作者应为 **`github-actions[bot]`**（这也是本次改动是否生效的最直接判据）。

---

## 6. 本地验证

```bash
# 20 个场景的离线测试（内置 stub 服务，不访问 GitHub，不发任何写请求）
node .github/scripts/dev-to-main-sync.test.mjs

# 变异测试：故意改坏计数/闸门/报告/凭据逻辑，验证测试套件确实会变红
node .github/scripts/dev-to-main-sync.test-mutations.mjs
```

覆盖范围（`dev-to-main-sync.test.mjs`，共 94 条断言）：

- 计数与闸门：未达阈值不建、刚好达阈值建（含 base/head 与正文断言）、已有 open 同步 PR 跳过、
  两分支无差异跳过、冷却期内跳过 / 冷却期外照建、`dry_run` 不建、锚点之前的 PR 不计入、
  `force` 只越过软闸门、无锚点回退窗口、缺 token 报错。
- 失败提示：Actions 被禁止建 PR 的 403 必须给出"去 Settings 开哪个开关"的原文提示、
  且**不得**再建议改用 PAT；token 缺 `pull-requests:write` 的 403 给出对症提示。
- 只报告步骤（`dev-to-main-sync-report.mjs`）：fork PR 委派 **退出码必须为 0**、
  "关闭未合并"退出码为 0、不该走到该步骤时仍退出 0 并给出警告。
- workflow 静态守卫：未加引号的 `run:` 纯量里不得出现会被 YAML 当注释的 `" #"`；
  `GH_TOKEN` 必须**只能**是内置 token 表达式（不得引用 `SYNC_PR_TOKEN`）；
  `permissions:` 必须显式声明 `contents: read` + `pull-requests: write`。
- 两条守卫各自都配了对照用例，断言它们**确实能抓住已发生的真实问题**（2026-10-10 的
  `run:` 截断那一行、以及旧的"PAT 优先"表达式），避免出现永远为真的假绿。

`dev-to-main-sync.test-mutations.mjs` 会先确认未改动的源码为**绿**，再逐一验证 8 个变异全部**变红**：
阈值闸门永远放行、阈值差一、忽略锚点、open PR 闸门失效、`dry_run` 失效、
只报告步骤改回非零退出、`run:` 里写回未加引号的 `#` 说明文字、**建 PR 凭据改回 PAT 优先**。

也可以对着真实仓库**只读**演练（只发 GET，`dry_run` 不会创建 PR）：

```bash
GH_TOKEN=<token> GITHUB_REPOSITORY=Samryetha-Development/Samryetha \
  SYNC_DRY_RUN=true node .github/scripts/dev-to-main-sync.mjs
```

---

## 7. 已知局限与未验证项

**未验证（无法在合并前验证，请勿当成已验证）**：

1. **端到端真实触发：功能已闭环，但本次"改用 bot 作者"的改动本身尚未线上验证**。
   2026-10-11 的首次真实生效已经证明整条链路可用：合入 `dev`（`5ed6360`）后 14 秒，
   `push` 运行判定 `count=10` 达阈值并自动建出 **PR #105**；同时 `pull_request` 的 fork 报告路径
   运行也是 **success**（即 2026-10-10 那个缺陷的修复已被线上验证）。
   但**本次改动（凭据换成 `GITHUB_TOKEN`、作者改为 `github-actions[bot]`）**要等下一次达到阈值、
   自动建出新 PR 时才能看到作者是否真的变成 bot —— 在那之前只有本地证据（用例 + 变异红绿），
   **不能说线上已验证**。
2. **`GITHUB_TOKEN` 能否建出 PR —— 未验证**。`GET /orgs/{org}/actions/permissions/workflow` 与
   仓库级同接口都返回 403（需 org admin / Actions policies 权限），**无法离线判断**该开关是否开着。
   注意：升级前那次成功的自动建 PR 用的是 PAT，因此**不能**作为"组织已允许 Actions 建 PR"的证据。
   验证方法：合并后下一次达到阈值时看运行是否成功、以及新 PR 的作者是否为 `github-actions[bot]`；
   若失败，日志会直接打印第 4.3 节那段提示。
3. **「`GITHUB_TOKEN` 建的 PR 不自动跑工作流」与「main 必过状态检查」共存 —— 预计满足，但未验证**。
   推理与实测证据见第 4.4 / 4.5 节（同一提交上已有来自 `push` 的同名成功检查），
   但"建 PR 时那个等待批准的运行会不会在提交上追加一组 pending 同名检查、从而把必过检查拖成 pending"
   **无法在本地或只读探测中确证**；第 4.5 节给了三步验证方法与两种应对办法。
4. **分支保护规则未能确证**。`GET /repos/.../branches/{dev,main}/protection` 均返回 404 ——
   由于我不是仓库 admin，**404 无法区分"确实没有保护"与"无权限查看"**，所以不能说 `dev`/`main`
   没有保护。rulesets 接口（`/rules/branches/{branch}`）可读且返回空数组，说明**没有规则集**，
   但传统 branch protection 的情况仍不确定。
5. **`run` 步骤的 shell 检查仍不完整**。actionlint 需要 `shellcheck` 才能查 shell 脚本，本机没有装，
   所以 `run:` 里的 shell 只做了 YAML/表达式/action 引用层面的检查。
   缓解措施：现在三条 `run:` 都只是 `node .github/scripts/...` 这种无特殊字符的命令，
   shell 负载全部搬进了可用 Node 直接测试的脚本文件；另加了针对
   "`run:` 未加引号纯量里出现 `" #"`" 的静态守卫（见第 6 节）。
   但**通用**的 shell 检查仍缺位，其它 workflow 不受该守卫保护。
6. **`workflow_dispatch` 只在默认分支 `main` 上可手动触发**，当前 `dev` 上还无法手动 dry-run，
   因此"先演练再启用"这一步得等本 workflow 随 `dev` 合入 `main` 之后才方便做。

**设计上接受的局限**：

7. **同一时间只允许一个未合并的同步 PR**。若管理员长期不合并，后续即使积累了更多 PR 也不会
   再建第二个（这是刻意的：避免 PR 堆积）。想强制再来一个，可先关掉现有 PR（随后受 24h 冷却期
   约束，或把 `SYNC_PR_COOLDOWN_HOURS` 设为 `0`）。
8. **锚点只认 `head.ref == dev`**。若有人从 fork 的 `dev` 分支开 PR 到 `main`，也会被当成一次同步
   锚点。本仓库历史上没有这种用法，故未做额外区分。
9. **`push` 与 `pull_request` 会对同一次合并各触发一次**。两次运行的结论一致（第二次通常会因
   "已有未合并的同步 PR"或"未达阈值"而跳过），代价是多一次只读的 Actions 运行；
   公开仓库的 Actions 分钟数不计费。
10. **不再有 PAT 逃生通道**。这是刻意的：保留"PAT 回退"会让作者身份随 Secret 配置变化，
    正是管理员要消除的行为。若将来确实遇到组织策略无法放开、又必须建 PR 的情况，
    备选方案是改用 GitHub App installation access token（作者为该 App 的 `<app>[bot]`），
    而不是回到 PAT。
