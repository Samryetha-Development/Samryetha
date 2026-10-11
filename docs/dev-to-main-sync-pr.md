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

## 4. 权限与 Secret

### 4.1 需要什么权限

- `contents: read` —— 读 PR 与分支比较。
- `pull-requests: write` —— 创建 PR。

### 4.2 用哪个 token

workflow 里的取值是：

```yaml
GH_TOKEN: ${{ secrets.SYNC_PR_TOKEN != '' && secrets.SYNC_PR_TOKEN || secrets.GITHUB_TOKEN }}
```

即：**配置了仓库 Secret `SYNC_PR_TOKEN` 就用它，否则退回内置 `GITHUB_TOKEN`**。

- **情况 A：组织允许 Actions 创建 PR** —— 不需要任何额外配置，`GITHUB_TOKEN` 直接可用。
- **情况 B：组织禁止 Actions 创建 PR** —— `GITHUB_TOKEN` 建 PR 会报
  `GitHub Actions is not permitted to create or approve pull requests`。
  此时需要管理员**在仓库配置一个 Secret：名字 `SYNC_PR_TOKEN`**，值为一个有权限的 PAT：
  - Fine-grained PAT：`Contents: Read`、`Pull requests: Read and write`，仓库范围选中本仓库；
  - 或 Classic PAT：`repo` 范围。
  - 说明：只需要"读 contents + 读写 pull requests"，**不需要** `admin`、不需要 `workflow` 范围。

> 脚本在遇到上述 403 时，会把 Secret 名与方法直接打进 Actions 日志和 Step Summary，
> 便于一眼定位，而不是丢一个裸的错误码。

---

## 5. 管理员上线步骤

1. **合并本 PR 到 `dev`**（这是前提：workflow 必须先在 `dev` 上存在才会被触发）。
2. **先演练**：Actions -> "Dev to main sync PR" -> Run workflow -> 保持 `dry_run = true` ->
   Run。看运行日志与 Step Summary 里的 `decision` / `count` / `threshold`。
   演练**不会**创建任何 PR。
3. **判断走 A 还是 B**：如果演练里出现上面那个 403，就去仓库 Settings 里加 `SYNC_PR_TOKEN`
   Secret；否则什么都不用配。
4. **（可选）调阈值**：按第 3 节设置仓库变量 `SYNC_PR_THRESHOLD`。
5. **正式启用**：`dry_run = false` 的手动运行可以直接强制发起一次同步；
   之后日常就由"PR 合入 dev"自动驱动，无需人工干预。
6. **观察**：每次运行都会在 Step Summary 里写明 `decision`（`created` / `would-create` /
   `skip`）、跳过原因、计数与阈值，方便判断是"还没到阈值"还是"被闸门挡住了"。

---

## 6. 本地验证

```bash
# 17 个场景的离线测试（内置 stub 服务，不访问 GitHub，不发任何写请求）
node .github/scripts/dev-to-main-sync.test.mjs

# 变异测试：故意改坏计数/闸门/报告逻辑，验证测试套件确实会变红
node .github/scripts/dev-to-main-sync.test-mutations.mjs
```

覆盖范围（`dev-to-main-sync.test.mjs`，共 78 条断言）：

- 计数与闸门：未达阈值不建、刚好达阈值建（含 base/head 与正文断言）、已有 open 同步 PR 跳过、
  两分支无差异跳过、冷却期内跳过 / 冷却期外照建、`dry_run` 不建、锚点之前的 PR 不计入、
  `force` 只越过软闸门、无锚点回退窗口、缺 token 报错、Actions 无权建 PR 时给出 `SYNC_PR_TOKEN` 提示。
- 只报告步骤（`dev-to-main-sync-report.mjs`）：fork PR 委派 **退出码必须为 0**、
  "关闭未合并"退出码为 0、不该走到该步骤时仍退出 0 并给出警告。
- workflow 静态守卫：未加引号的 `run:` 纯量里不得出现会被 YAML 当注释的 `" #"`；
  另有一条对照用例，断言该守卫**确实能抓住 2026-10-10 真实故障的那一行**。

`dev-to-main-sync.test-mutations.mjs` 会先确认未改动的源码为**绿**，再逐一验证 7 个变异全部**变红**：
阈值闸门永远放行、阈值差一、忽略锚点、open PR 闸门失效、`dry_run` 失效、
只报告步骤改回非零退出、`run:` 里写回未加引号的 `#` 说明文字（复现真实故障）。

也可以对着真实仓库**只读**演练（只发 GET，`dry_run` 不会创建 PR）：

```bash
GH_TOKEN=<token> GITHUB_REPOSITORY=Samryetha-Development/Samryetha \
  SYNC_DRY_RUN=true node .github/scripts/dev-to-main-sync.mjs
```

---

## 7. 已知局限与未验证项

**未验证（无法在合并前验证，请勿当成已验证）**：

1. **端到端真实触发：部分已验证，修复本身的这一条路径仍未验证**。
   2026-10-10 的首次真实运行已经证明：`push` 路径（决策 + 自测）**确实跑通**，
   并给出 `decision=skip reason=below-threshold:8/10`、未创建任何 PR；同时也证明
   `pull_request` 的 fork 报告路径当时是坏的。修复后的**该路径**要等下一次有 fork PR
   合入 `dev` 才会再次被真实触发——在那之前，只能说它已被本地用例与变异检查覆盖，
   不能说线上已验证。
2. **组织是否允许 Actions 创建 PR —— 读不到**。`GET /orgs/{org}/actions/permissions/workflow` 与
   仓库级同接口都返回 403（需 org admin / Actions policies 权限），因此**无法离线判断**。
   另外 `workflow_dispatch` 只在默认分支 `main` 上可手动触发，当前 `dev` 上还无法手动 dry-run。
3. **分支保护规则未能确证**。`GET /repos/.../branches/{dev,main}/protection` 均返回 404 ——
   由于我不是仓库 admin，**404 无法区分"确实没有保护"与"无权限查看"**，所以不能说 `dev`/`main`
   没有保护。rulesets 接口（`/rules/branches/{branch}`）可读且返回空数组，说明**没有规则集**，
   但传统 branch protection 的情况仍不确定。若 `main` 上存在必需评审/必需状态检查，
   自动 PR 仍会被正常挡住，需要人工放行，这属于预期行为。
4. **`run` 步骤的 shell 检查仍不完整**。actionlint 需要 `shellcheck` 才能查 shell 脚本，本机没有装，
   所以 `run:` 里的 shell 只做了 YAML/表达式/action 引用层面的检查。
   缓解措施：现在三条 `run:` 都只是 `node .github/scripts/...` 这种无特殊字符的命令，
   shell 负载全部搬进了可用 Node 直接测试的脚本文件；另加了一条针对
   "`run:` 未加引号纯量里出现 `" #"`" 的静态守卫（见第 6 节）。
   但**通用**的 shell 检查仍缺位，其它 workflow 不受该守卫保护。

**设计上接受的局限**：

5. **同一时间只允许一个未合并的同步 PR**。若管理员长期不合并，后续即使积累了更多 PR 也不会
   再建第二个（这是刻意的：避免 PR 堆积）。想强制再来一个，可先关掉现有 PR（随后受 24h 冷却期
   约束，或把 `SYNC_PR_COOLDOWN_HOURS` 设为 `0`）。
6. **锚点只认 `head.ref == dev`**。若有人从 fork 的 `dev` 分支开 PR 到 `main`，也会被当成一次同步
   锚点。本仓库历史上没有这种用法，故未做额外区分。
7. **`push` 与 `pull_request` 会对同一次合并各触发一次**。两次运行的结论一致（第二次通常会因
   "已有未合并的同步 PR"或"未达阈值"而跳过），代价是多一次只读的 Actions 运行；
   公开仓库的 Actions 分钟数不计费。
