# Samryetha 文件服务（File Service）方案设计

- 分支：`feat/file-service-20261007`（基于 `origin/dev` 的 `5f687f2`，版本 0.2.0）
- 定位：面向**新学生**的实用资料库板块 —— 新生攻略、各科复习提纲、学习纲要、历年题等
- 本文档：技术选型、数据模型、接口设计、页面结构、UI 参考与取舍理由、分期实施计划

---

## 1. 现状侦察（动手前读代码的结论）

### 1.1 技术栈实况（重要：仓库 AGENTS.md 曾长期描述错误的旧栈）

仓库根 `AGENTS.md` 曾经描述的是 Node/Fastify + Drizzle 的旧架构，**与磁盘上的真实代码不符**。
截至本次分支的基线 `origin/dev`（0.2.0），真实技术栈是：

| 层次 | 真实实现 | 位置 |
| --- | --- | --- |
| 后端语言 | Python 3.12+（无构建步骤，源码直跑） | `backend/src/samryetha/` |
| 后端框架 | FastAPI + Starlette + SQLAlchemy 2.x（同步 Connection） | `backend/src/samryetha/main.py` |
| 数据库 | SQLite（WAL / foreign_keys=ON / busy_timeout=5000） | `backend/src/samryetha/db.py` |
| Schema 真源 | `schema.py` 声明式 Table；**无迁移框架** | `backend/src/samryetha/core/schema.py` |
| 建表/补列 | 启动时 `create_schema()`（create_all，幂等）+ `ensure_schema_drift()`（幂等 ADD COLUMN / CREATE INDEX） | `db.py:88-143` |
| 前端 | React 19 + Vite 8 SSR + Tailwind 4 | `frontend/src/` |
| SSR 入口 | `entry-server.tsx` → `RootApp`（自研 SPA 路由，非 react-router） | `frontend/src/root-app.tsx` |
| 导航 | `TOP_NAV_LINKS` 常量数组 | `frontend/src/top-nav.tsx` |
| UI 组件库 | 本地包 `samryetha-ui-commons`（Dialog/ConfirmDialog 等）、`@lako/ui` | `packages/ui-commons/` |
| 样式 | 单一全局样式表 | `frontend/src/globals.css`（约 113 KB） |
| 文案 | `frontend/src/lib/locales/en.ts` + `zh-CN.ts` 为真源，仅中英两种 | `frontend/src/lib/locales/` |
| 测试 | pytest（后端，`backend/tests/`）、`tsc --noEmit`（前端） | — |

### 1.2 鉴权与授权体系（必须复用，不得另起炉灶）

- **鉴权（Authentication）**：Cookie 会话。`deps.py` 提供依赖注入链
  `get_current_user` → `require_user` → `require_active_user` → `require_admin` / `require_moderator`。
  会话令牌只存哈希（`sessions.token_hash`），解析入口 `security.get_session_user`。
- **授权（Authorization）**：唯一入口 `authz/service.py` 的 `AuthorizationService(conn).can(actor, ability, resource)` / `assert_can(...)`。
  约定是**业务代码不散落 `user.role == ...` 判断**，一律走 ability 字符串常量。
  资源以鸭子类型对象传入（`type` 字段 + 该 ability 需要的其它字段）。
- 既有角色：`student | moderator | admin`；但 `moderator` 已并入 `admin`
  （见 `authz.is_global_mod`），`require_moderator` 仅为兼容保留。
- 账号状态：`pending | active | banned | deactivated`。写操作一律要求 `active`。

### 1.3 文件基础设施（已存在，直接复用）

`adapters/storage.py` 已提供一套完整的本地磁盘对象存储 + HMAC presign，语义与 S3 presigned URL 对齐：

- `Storage.create_upload_session()`：校验扩展名白名单，生成 `{uuid}/{安全文件名}` 形式的 objectKey。
- `generate_upload_url()` / `generate_download_url()`：签名 = `HMAC-SHA256("{method}|{pathname}|{expires}")`。
- `verify_signature()`：常量时间比较 + 过期校验。
- `path_for()`：`realpath` + `commonpath` 双重校验，防目录穿越。
- `content_type_for_object_key()`：**按扩展名推导 MIME，绝不信任客户端声明**（防存储型 XSS）。
- 约束：`MAX_UPLOAD_BYTES = 50 MiB`；扩展名白名单已覆盖 `pdf/doc/docx/xls/xlsx/ppt/pptx/zip/rar/7z/md/txt/csv` 与常见图片音视频 —— **对学习资料场景足够，本期不需要扩白名单**。

`routers/attachments.py` 已实现 presign → signed PUT → signed GET 三段式，
包括「按 presign 声明的体积二次收紧上限」「上传时复核上传者仍是 active」「上传签名不证明上传者可用」等安全细节。
**文件服务沿用同一套存储层与三段式流程，但另开自己的 serve 路由**（见 §4.3 的取舍说明）。

### 1.4 现有同类板块可作模板

- 后端：`boards.py`（service 风格）、`routers/tasks.py`（简单 CRUD + 权限）
- 前端：`tasks-page.tsx` / `feedback-page.tsx`（列表 + 筛选 + 弹窗表单 + AppShell 外壳）
- 二者都是「独立顶栏入口 + 独立页面」的既有范式，文件服务照此办理。

---

## 2. 目标与范围

### 2.1 目标用户与核心场景

主用户是**刚入学的新生**，其典型诉求按优先级排序：

1. 「我刚来，学校什么样、要准备什么」→ 新生攻略（一次性、强时效、需要置顶）
2. 「这门课怎么复习、考什么」→ 各科复习提纲 / 历年题（按科目+学期组织、需要搜索）
3. 「这门课的知识框架」→ 学习纲要 / 笔记（长期沉淀、需要评分与版本）

### 2.2 本期做 / 不做

**做**：浏览、分类、标签、搜索、筛选排序、详情、下载、收藏、评分、上传贡献、权限可见性、管理员分类管理。

**不做（并写明理由）**：

| 不做项 | 理由 |
| --- | --- |
| 积分 / 打赏 / 付费下载 | 校园站点无商业化诉求；引入积分会带来刷分、通胀调节等大量运营负担 |
| 网盘外链跳转 | 破坏「站内可控」与「下载量可信」；外链失效与安全风险不可控 |
| Office 在线预览（docx/pptx 转 PDF） | 需引入 LibreOffice 或云端转换服务，运维成本与攻击面远超收益。本期只对 PDF / 图片 / 纯文本做内联预览 |
| 多级无限分类 | 学生资料天然是「类别 + 科目 + 学期」三维，多级目录会迅速失控；用固定类别 + 自由标签替代 |
| 版本历史（v2 起） | 有价值但非首期必需；数据模型预留 `version` 字段与独立版本表，P4 再落地 |

---

## 3. UI / 信息架构参考与取舍

> 详细分平台观察见同目录 `ui-research-communities.md`（523 行，含逐平台事实、横向对比表、来源清单）。
> 本节是**结论性清单**，下方表格已与调研报告逐条对照过；凡调研给出了实证依据的条目都标注了证据，
> 凡与本方案存在分歧的条目都单列在 §3.4，不做静默忽略。
>
> 调研的可验证边界（重要，避免后续会话重复投入）：**实际抓到正文的平台**为 Discourse Meta 官方文档、
> GitHub Releases、Notion 帮助中心、豆瓣小组、百度贴吧帮助中心、百度网盘企业版、蓝奏云官网；
> **被反爬拦截、因此在本项目内标注为「未能验证」** 的是 Reddit、Stack Exchange、NGA、知乎（Cloudflare 403）、
> V2EX（超时）、CSDN 下载（HTTP 521）、Google Drive（超时），公共阅读代理 r.jina.ai 亦不可达。
> 这些通道请勿重复尝试。

### 3.1 采用（Adopt）

| 编号 | 借鉴来源 | 采用的交互 | 本项目落地方式 |
| --- | --- | --- | --- |
| A1 | Discourse 分类+标签体系 | 一级分类 + 自由标签并存 | `file_categories`（固定分类）+ `file_resources.tags`（JSON 数组，最多 8 个） |
| A2 | NGA / 贴吧 版块页 | 顶部固定「置顶/精华」区，下方常规列表 | 列表页顶部「新生专区」横条（kind=guide 且 is_featured） |
| A3 | V2EX / Reddit | 列表行密度高、信息字段紧凑 | 表格型列表：标题 / 分类 / 大小 / 格式 / 上传者 / 时间 / 下载量 |
| A4 | Discourse Topic 列表 | 排序维度切换（最新 / 最热 / 最多下载 / 最高评分） | `sort` 参数四值 |
| A5 | GitHub Releases | 元信息区结构化呈现（大小、格式、更新日期、校验值） | 详情页 keyvalue 元信息卡，含 `sha256` 前 12 位 |
| A6 | Stack Exchange | 下载前的登录态提示与权限说明 | 未登录点击下载 → 内联提示而非弹层，说明「登录后可下载」 |
| A7 | 各站通用 | 收藏夹（个人维度） | `file_favorites` 表 + 「我的收藏」标签页 |
| A8 | 豆瓣 / CSDN 资源 | 星级评分（1–5） | `file_ratings` 表，一人一票可改；详情页显示均分与人数。**注意：调研明确不建议采用，本方案保留但降级为次要信号——见 §3.4 分歧披露** |
| A10 | 各站通用 | 面包屑 + 返回 | 详情页面包屑「文件服务 / 分类 / 标题」 |
| A11 | Google Drive 分享页 | 可见性三档 | `public`（所有人）/ `members`（登录用户）/ `private`（仅上传者与管理员） |

### 3.2 放弃（Reject）及理由

| 编号 | 放弃项 | 来源 | 理由 |
| --- | --- | --- | --- |
| R1 | 「回复可见」下载门槛 | 贴吧 / NGA | 制造大量无意义灌水回复，污染讨论区；对新生极不友好（他们正是最需要资料的人） |
| R2 | 需积分/等级才能下载 | CSDN / 部分资源站 | 与「面向新学生、提供实用性资料」的定位直接冲突 |
| R3 | 无限层级目录树 | 传统网盘 / 文库 | 导航成本随层级指数上升；三维需求用「分类 + 标签 + 搜索」三件套覆盖更好 |
| R4 | 卡片瀑布流（大封面图） | 视觉型资源站 | 学习资料的价值在元信息与可检索性，不在封面图；卡片流会显著降低一屏信息量 |
| R5 | 评论区（独立讨论帖） | 各站资源页 | 主站已有完整讨论区（`discussions`/`replies`）。资料页只做**评分**，讨论引导回主站版块，避免两套评论体系 |
| R6 | 打赏 / 付费 | 资源站 | 见 §2.2 |
| R7 | 网盘外链 | 网盘分享页 | 见 §2.2 |
| R8 | 无限式自由标签（无规范化） | 早期 Discourse | 会产生「高数 / 高等数学 / 微积分」同义分裂；本期标签做**统一小写 + 去重 + 长度上限**，并对高频标签在分类页做固定入口。调研进一步指出 Discourse 社区内部即有「标签被滥用、普通用户不知道怎么用」的抱怨，故 P4 追加受控词表与管理员批量改标签 |
| R9 | 提取码 / 密码式分享保护 | 百度网盘 / 蓝奏云 | 调研证据：Notion 官方明确不提供页面密码保护，而以「逐个邀请」替代。校内场景中登录态与分组权限本就是更强的身份边界，再加一道提取码只增加摩擦、几乎不增加安全性 |
| R10 | 三级以上分类树 | Discourse | 调研证据：Discourse 出于可维护性**主动把分类限制在两级**，其社区实践者亦指出「大量分类与层层子分类几乎无法管理，而且分类并不帮助用户找到相关内容」 |

### 3.3 关键交互决策（需要单独说明的三条）

1. **列表用表格而不是卡片**（对应 A3/R4）。理由：一屏可扫 15–20 条 vs 卡片流 4–6 条；
   学习资料的核心决策依据是「科目 + 格式 + 时间 + 口碑」，全是文本字段，图形化无增益。
2. **搜索与筛选是并列的一等公民，不是二级入口**（对应 A1/A4）。
   新生不知道自己要找的东西叫什么名字，所以「分类点进去 + 标签收窄 + 关键词搜索」三条路径必须同时在首屏可见。
3. **下载前的权限提示用内联文案而非弹层**（对应 A6）。
   理由：弹层会打断浏览节奏；内联提示（「登录后可下载」+ 登录按钮）让用户在同一屏内完成决策与动作。

### 3.4 调研结论与本方案的差异对照（不做静默忽略）

调研报告第 3 节给出 29 条可借鉴点。逐条核对后，本方案与调研的差异只有以下三类，全部在此披露：

**（一）功能取舍上的真实分歧：星级评分**

- 调研立场（不建议采用，理由为「Discourse 与 GitHub 均未对内容采用星级评分；学生资料的评分样本量小、
  主观性强，点赞与下载量更稳健」）。
- 本方案处理：**保留评分，但把它明确降级为次要信号**，具体做法有三条——
  1. 一等排序维度改为「最新 / 下载最多 / 收藏最多」，其中「收藏最多」正是按调研建议新增的
     （后端 `SORTS` 含 `favorites`，界面排序下拉可见）；评分排序仍在，但不作为默认。
  2. 列表页的评分列在无人评分时显示「暂无评分」，而不是显示 0 分，避免用零值误导判断。
  3. 保留而非删除的额外成本极低：表结构已建、测试已覆盖，若管理员后续认定应严格遵循调研结论，
     **只需隐藏前端的评分 UI，不必回滚任何数据模型或迁移**。
- 诚实结论：这是一处**有意为之的偏离**，不是对调研的无视。若管理员要求完全对齐证据，按上一条即可收敛。

**（二）调研建议但本期未做、已进 P4 待办的功能**

| 调研条目 | 内容 | 进 P4 的理由 |
| --- | --- | --- |
| 第 6 条 | 受控词表 + 管理员批量改标签 | 本期已做归一化（小写/去重/限长限量），但批量收敛工具需要额外管理界面，不阻塞主流程 |
| 第 7 条 | 可见性增加「指定分组（班级/专业/社团）可见」 | 本期三档（公开/登录/仅自己）已落地；分组需要引入组实体，属结构性改动，单独一期更稳 |
| 第 9、10 条 | 举报闭环（入口/时效/结果可查）+ 多人举报阈值自动隐藏 | 既有 `reports` 表的 `reportable_type` 是文本列，追加 `file_resource` 无需改表；但完整闭环含自动隐藏与通知，是独立的一期工作量 |
| 第 12 条 | 版本历史（同一资料多版本） | 数据模型已预留 `version` 字段与独立版本表位，界面留到 P4 |
| 第 13 条 | 资料合集/系列 | 调研本身也提示「合集很容易退化成又一层分类」，需谨慎设计边界 |
| 第 29 条 | 课程代码字段（与教务信息对接位） | 调研标注为【推断】；一旦字段定型迁移成本高，先只记录待办，不贸然加列 |

**（三）调研未覆盖、由本方案自行判断的**：新生专区的具体形态（一期做成「按 kind=guide 取下载最多 4 条」
的横条，而非新开一级板块）。调研第 27 条对此只给了【推断】，且该条推断与本方案一致；
本方案在其基础上补了一条调研没有的约束——**横条容量固定为 4 条**，理由是横条一旦变长就会挤占主列表，
反而伤害「快速找到资料」这个首要任务。

---

## 4. 数据模型

### 4.1 设计约定（严格对齐既有 schema.py）

- 所有时间戳是 **epoch 毫秒整数**（`BigInteger`，helper `_ms()`），不用 SQLAlchemy DateTime。
- 布尔/位标记是**整数 0/1**。
- JSON 列存 **JSON TEXT**（如 `tags`）。
- 软删除复用 `_soft_delete()` helper → `deleted_at / deleted_by / deletion_reason` 三列。
- 主键自增且 `sqlite_autoincrement=True`。
- **新增列必须能被 `ensure_schema_drift` 安全 ADD**：非 PK、非 UNIQUE、NOT NULL 必须带 `server_default`，
  否则存量库升级时 `db.py:120-123` 会直接抛 RuntimeError 拒绝启动。

### 4.2 表结构

#### `file_categories` — 资料分类

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | Integer PK | |
| slug | Text NOT NULL UNIQUE | 稳定标识，如 `freshman-guide`、`exam-outline` |
| name | Text NOT NULL | 展示名，如「新生攻略」「复习提纲」 |
| description | Text NOT NULL DEFAULT '' | 分类说明 |
| kind | Text NOT NULL DEFAULT 'other' | `guide`(新生攻略) / `outline`(复习提纲) / `syllabus`(学习纲要) / `exam`(历年题) / `other` |
| sort_order | Integer NOT NULL DEFAULT 0 | 手工排序，越小越靠前 |
| is_system | Integer NOT NULL DEFAULT 0 | 内建分类不可删除（仅可改名/排序） |
| deleted_at / deleted_by / deletion_reason | | 软删除 |
| created_at / updated_at | BigInteger | |

索引：`file_categories_kind_idx(kind)`、`file_categories_sort_idx(sort_order, id)`。

#### `file_resources` — 资料条目（核心表）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | Integer PK | |
| category_id | FK file_categories.id NOT NULL | 所属分类 |
| uploader_id | FK users.id NOT NULL | 上传者 |
| title | Text NOT NULL | 标题 |
| description_md | Text NOT NULL DEFAULT '' | 说明（Markdown，复用既有 markdown 渲染） |
| tags | Text NOT NULL DEFAULT '[]' | JSON 数组，归一化小写、去重、最多 8 个、单个 ≤ 24 字符 |
| object_key | Text NOT NULL UNIQUE | 存储对象键，形状同 attachments（`{uuid}/{filename}`） |
| original_filename | Text NOT NULL | 原始文件名 |
| mime_type | Text NOT NULL | 入库声明（**仅供展示，回源一律按扩展名推导**） |
| size_bytes | Integer NOT NULL | 体积 |
| sha256 | Text | 上传后计算，供完整性校验 |
| visibility | Text NOT NULL DEFAULT 'members' | `public` / `members` / `private` |
| status | Text NOT NULL DEFAULT 'published' | `published` / `archived` |
| version | Integer NOT NULL DEFAULT 1 | 版本号，为 P4 版本历史预留 |
| is_featured | Integer NOT NULL DEFAULT 0 | 精选/置顶（新生专区横条用） |
| download_count | Integer NOT NULL DEFAULT 0 | 下载计数（冗余列，避免列表页 JOIN 聚合） |
| favorite_count | Integer NOT NULL DEFAULT 0 | 收藏数（冗余列） |
| rating_sum | Integer NOT NULL DEFAULT 0 | 评分总和（冗余列） |
| rating_count | Integer NOT NULL DEFAULT 0 | 评分人数（冗余列） |
| deleted_at / deleted_by / deletion_reason | | 软删除 |
| created_at / updated_at | BigInteger | |

索引（对齐既有命名风格）：
- `file_resources_category_created_idx(category_id, created_at)`
- `file_resources_category_download_idx(category_id, download_count)`
- `file_resources_uploader_created_idx(uploader_id, created_at)`
- `file_resources_featured_idx(is_featured, created_at)`
- `file_resources_status_idx(status)`

**冗余计数列的取舍说明**：`download_count` 等四列为冗余。
理由：列表页是最高频查询，若每次都对明细表做 `COUNT/SUM` 聚合，在资料量增长后会明显拖慢首屏；
且这四个值只增不减的语义清晰，维护点集中在本模块 service 层，一致性风险可控。
代价：写入路径必须与明细表同事务更新，已在 service 层统一封装。

#### `file_favorites` — 收藏

`user_id` + `resource_id` 复合主键，`created_at`。索引：`file_favorites_resource_idx(resource_id)`。
之所以也建反向索引：详情页要算 `favorite_count`，而 `favorite_count` 也可由冗余列提供，
反向索引用于「某资料的收藏者列表」这类管理场景。

#### `file_ratings` — 评分

`user_id` + `resource_id` 复合主键，`score` Integer NOT NULL（1–5），`created_at`、`updated_at`。
一人一票，改分即 UPDATE。索引：`file_ratings_resource_idx(resource_id)`。

#### `file_downloads` — 下载明细（用于防刷与审计）

| 列 | 类型 |
| --- | --- |
| id | Integer PK |
| resource_id | FK file_resources.id NOT NULL |
| user_id | FK users.id（NULL = 未登录的公开下载） |
| created_at | BigInteger |

索引：`file_downloads_resource_created_idx(resource_id, created_at)`、`file_downloads_user_idx(user_id)`。

**计数去重策略**：`download_count` 只在「该 user 对该 resource 的首次下载」时 +1；
未登录下载（仅 `public` 可见性允许）按「IP + 资源 + 24 小时窗口」去重。
明细表保留全部记录（含重复），供后续审计与防刷分析——**计数是脸面，明细是证据**，两者不可互相替代。

#### 与 `attachments` 表的关系（重要取舍）

**不复用 `attachments` 表，另建 `file_resources`。** 理由三条：
1. `attachments` 的语义是「讨论帖/回复的附件」，其可见性由父帖推断（`attachments.downloadable` 要回查 discussion 的权限和删除状态）。资料库是**独立的一等内容**，可见性由自身字段决定，硬塞进去会让 `downloadable` 长出两套互斥分支。
2. `attachments` 无标题、无分类、无标签、无评分——资料库需要的字段远超它，加列会让该表语义分裂。
3. `attachments` 有存亡周期（`reap_orphans` 会回收 `pending`/`orphaned` 行）。资料库条目是长期资产，不应被附件回收逻辑扫描。

**复用点在存储层而非表层**：`object_key` 形状一致、同一个 `Storage` 实例与同一套 presign 签名算法。
这带来一个好处：未来若要做「附件转存为资料」，只需搬 objectKey，不必搬文件。

---

## 5. 接口设计

统一约定：全部挂 `/api/files/*`；错误走既有 envelope `{error:{code,message,requestId,details?}}`；
写操作要求 `active` 用户；列表接口对**不可见资源一律不出现**（而非返回后前端过滤）。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/files/config` | 公开 | 下发扩展名白名单、体积上限、分类列表、标签云（前端免手抄常量） |
| GET | `/api/files/categories` | 公开 | 分类列表（含每类资源数） |
| GET | `/api/files/resources` | 公开（按可见性过滤） | 列表。参数：`category` `tag` `q` `sort` `kind` `uploader` `page` `pageSize` |
| GET | `/api/files/resources/{id}` | 公开（按可见性过滤） | 详情，含 `can{update,delete}`、`isFavorited`、`myRating` |
| POST | `/api/files/resources/presign` | active | 申请上传会话，返回 presigned PUT URL |
| POST | `/api/files/resources` | active | 上传完成后创建条目（携带 objectKey 与元数据） |
| PATCH | `/api/files/resources/{id}` | 上传者 / admin | 改标题、说明、分类、标签、可见性 |
| DELETE | `/api/files/resources/{id}` | 上传者 / admin | 软删除 + 释放磁盘对象 |
| GET | `/api/files/resources/{id}/download` | 按可见性 | 记录下载明细并 302 到带签名的 serve URL |
| PUT | `/api/files/uploads/{object_key}` | 签名 | 实际接收字节（对齐 attachments 的 PUT 语义） |
| GET | `/api/files/serve/{object_key}` | 签名 + 可见性复核 | 实际回源字节 |
| PUT | `/api/files/resources/{id}/favorite` | active | 收藏（幂等） |
| DELETE | `/api/files/resources/{id}/favorite` | active | 取消收藏（幂等） |
| GET | `/api/files/favorites` | active | 我的收藏 |
| PUT | `/api/files/resources/{id}/rating` | active | 评分（1–5，覆盖式） |
| DELETE | `/api/files/resources/{id}/rating` | active | 撤销评分 |
| GET | `/api/files/mine` | active | 我上传的资料（含归档） |
| POST | `/api/files/categories` | admin | 新建分类 |
| PATCH | `/api/files/categories/{id}` | admin | 改分类 |
| DELETE | `/api/files/categories/{id}` | admin | 软删除分类（`is_system` 拒绝，非空分类需先迁移或强制级联软删） |

### 5.1 关键接口细节

**`GET /api/files/resources` 排序枚举**：`latest`（创建时间倒序，默认）/ `downloads` / `rating` / `name`。
分页用 `page`（从 1 起）+ `pageSize`（默认 20，上限 50），返回 `{items, total, page, pageSize}`。

**`POST /api/files/resources/presign`** 复用 `Storage.create_upload_session`，
额外校验：分类存在且未删除；`category` 必填。返回 `{resourceToken, uploadUrl, method, headers}`。
`resourceToken` 是短期令牌，把「本次 presign 的元数据」与「objectKey」绑定，
避免创建接口被塞入任意 objectKey 去挂一个不属于自己的文件。

**`GET /api/files/resources/{id}/download`** 的三步：
1. 按 `visibility` + 当前 viewer 判定可下载性；不可见 → **404（不是 403）**，不泄漏资源存在性。
2. 若不是 `public` 且 viewer 为 None → 401，前端据此展示内联登录提示。
3. 写 `file_downloads` 明细（按 §4.2 去重规则决定是否 +1 `download_count`），
   生成下载签名并 302 重定向到 `/api/files/serve/...`。

### 5.2 授权能力（加入 `authz/service.py`）

新增 ability 常量，保持「授权唯一入口」不被破坏：

- `FILE_READ`：`public` 任何人；`members` 需 active 用户；`private` 仅上传者与 admin。
- `FILE_CREATE`：active 用户。
- `FILE_UPDATE` / `FILE_DELETE`：上传者本人或 admin。
- `FILE_MANAGE_CATEGORY`：admin。

`can()` 中按既有风格加分支，resource 传 `{"type":"file_resource", ...}` 的鸭子对象。
**不新增角色**——沿用 `student | admin` 两级，避免权限体系分叉。

---

## 6. 前端页面结构

### 6.1 路由与导航入口（硬要求 1）

- 新路由：`/files`（列表）、`/files/{id}`（详情）。
- 入口：在 `frontend/src/top-nav.tsx` 的 `TOP_NAV_LINKS` 中插入
  `{ href: "/files", labelKey: "nav.files" }`，位置放在「反馈」之前（与「最新/关注/板块」三个内容型入口同组）。
- 同时改 `root-app.tsx` **两处**：
  1. `navigate()` 里的 `isApp` 白名单（否则点击会被当作外部链接放行走整页跳转，丢失 SPA 体验）；
  2. 页面分发链（`activePath === "/files"` → `FilesPage`，`/files/{id}` 用正则 `FILE_DETAIL_PATTERN`）。
- 移动端：`mobile-menu.tsx` 的入口由 `TOP_NAV_LINKS` 派生，无需另改（需在实现时确认）。

### 6.2 列表页 `/files`

```
┌ 顶栏（AppShell：wordmark / 主导航（含"文件服务"高亮） / 搜索 / 用户菜单 / 发布）┐
├ 页头：标题「文件服务」+ 一句话定位 + [上传资料] 按钮（未登录→登录）          │
├ 新生专区横条：is_featured 且 kind=guide 的 3–5 条（无数据时整条隐藏）        │
├ 工具条：分类 tab（全部/新生攻略/复习提纲/学习纲要/历年题/其他）              │
│         + 关键词搜索框 + 排序下拉（最新/最多下载/最高评分/名称）              │
│         + 标签云（高频前 12 个，点击收窄）                                    │
├ 资料表格：标题 │ 分类 │ 格式 │ 大小 │ 上传者 │ 时间 │ 下载 │ 评分             │
├ 分页                                                                          │
└ 页脚                                                                          │
```

- 空态、加载中、加载失败三态都要有（对齐 `tasks-page.tsx` 的 `loadError` 模式）。
- 未登录可见 `public` 资源；`members` 资源显示但标注「登录后可下载」——**列表可见、下载受限**，
  这是对新生友好的关键：先让他们看到「这里有什么」，再引导登录。

### 6.3 详情页 `/files/{id}`

```
面包屑：文件服务 / 复习提纲 / 高等数学（上）期末提纲
标题 + 分类 + 可见性徽章
元信息卡：文件名 │ 格式 │ 大小 │ 上传者 │ 上传时间 │ 下载次数 │ 评分 │ SHA-256 前 12 位
说明（Markdown 渲染，复用 MarkdownText）
预览区：PDF / 图片 / 纯文本内联预览；其它格式显示「此格式不支持在线预览」
操作区：[下载] [收藏] [评分 ★★★★★]
```

### 6.4 新增/修改的前端文件

| 文件 | 动作 |
| --- | --- |
| `frontend/src/files-page.tsx` | 新建，列表页 |
| `frontend/src/file-detail-page.tsx` | 新建，详情页 |
| `frontend/src/top-nav.tsx` | 修改，加导航项 |
| `frontend/src/root-app.tsx` | 修改，注册路由（isApp 白名单 + 页面分发） |
| `frontend/src/lib/api.ts` | 修改，加 `api.files.*` 客户端方法 |
| `frontend/src/lib/locales/en.ts` | 修改，加 `file.*` / `nav.files` 文案 |
| `frontend/src/lib/locales/zh-CN.ts` | 修改，同上（中文） |
| `frontend/src/globals.css` | 修改，追加文件服务样式段 |

---

## 7. 后端文件清单

| 文件 | 动作 |
| --- | --- |
| `backend/src/samryetha/core/schema.py` | 修改，加 5 张表到 `metadata` 与 `__all__` |
| `backend/src/samryetha/files/service.py` | 新建，FileService 用例 + FileRepository 持久化，连接/事务由调用者持有 |
| `backend/src/samryetha/files/router.py` | 新建，路由层 |
| `backend/src/samryetha/main.py` | 修改，`include_router(files_router)` |
| `backend/src/samryetha/authz/service.py` | 修改，加 `FILE_*` ability 与 `can` 分支 |
| `backend/tests/test_files.py` | 新建，pytest 用例 |
| `backend/docs/api-contract.md`、`schema.md` | 修改，同步接口与表结构（仓库规范要求） |

---

## 8. 分期实施计划（每期可独立验收）

### P1 — 骨架与只读浏览（可独立验收）

**产物**：5 张表 + `files/service.py` + `files/repository.py`（只读部分）+ `files/router.py`（GET 系列）+
分类种子数据 + 列表页 + 详情页 + 导航入口 + i18n 文案。

**验收标准**：
1. `uv run pytest` 全绿（含新增用例）。
2. `cd frontend && pnpm typecheck` 通过。
3. 后端启动后 `GET /api/files/categories`、`GET /api/files/resources` 返回符合契约的 JSON。
4. 浏览器访问站点，**主导航能看到「文件服务」入口**，点击进入 `/files`，列表与详情可正常渲染。
5. 空数据时页面呈现空态而非报错。

### P2 — 上传与贡献流程（可独立验收）

**产物**：presign / 创建 / 编辑 / 删除接口 + 上传弹窗 + 「我的上传」页 + 下载计数。

**验收标准**：登录用户能完整走通「选文件 → 校验 → 上传 → 填元数据 → 发布 → 出现在列表 → 下载」；
非上传者调 PATCH/DELETE 返回 403；超限体积与非法扩展名被拒。

### P3 — 互动与可发现性（可独立验收）

**产物**：收藏、评分、标签云、新生专区横条、相关推荐（同分类同标签）。

**验收标准**：收藏与评分幂等；均分与人数正确；同一用户重复收藏不产生重复行。

### P4 — 运营与增强（可独立验收）

**产物**：分类管理后台、精选置顶、版本历史表与界面、（可选）PDF 内联预览增强。

**验收标准**：管理员可增删改分类；`is_system` 分类不可删；版本历史可回溯。

---

## 9. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 存量库升级时新列导致启动失败 | 严格遵守 §4.1：所有新增列带 `server_default`，非 PK/UNIQUE；升级前在库副本上演练 |
| 冗余计数列与实际明细不一致 | 写入路径集中在 service 层单一函数内，与明细表同事务更新；提供管理员重算接口 |
| 标签同义分裂 | 统一小写 + 去重 + 长度上限；高频标签做固定入口；后续可加管理员合并工具 |
| 上传滥用（体积/数量） | 复用 50 MiB 上限与扩展名白名单；`/api/files/resources` 创建走 `GuardMiddleware` 全局限频；P2 追加每用户日配额 |
| 导航改动破坏既有 SPA 路由 | 改 `root-app.tsx` 时同步 `isApp` 白名单与分发链两处，改完逐条回归既有路由（`/`、`/d/{id}`、`/feedback`、`/tasks`、`/inbox`） |
| 文档漂移（AGENTS.md 已漂移过一次） | 本期同步更新 `backend/docs/api-contract.md` 与 `schema.md`；并在实现完成时校正仓库根 `AGENTS.md` 中已过时的 Node/Fastify 描述 |

---

## 11. 验证状态（诚实记录：哪些验过、哪些没验）

| 项目 | 状态 | 证据 |
| --- | --- | --- |
| 后端完整回归 | **已验证** | `pytest` 全量 **372 passed**（317 条既有基线 + 25 条功能用例 + 24 条安全与边界自检 + 6 条评审意见回归），0 失败 |
| PR 评审意见（F01/F03/F04/F05/F06） | **已验证修复** | 逐条复现并修复；6 条后端回归用例 + 浏览器级 F05/F06 用例，均已实证"停用修复→用例失败→恢复→通过"。F02 由评审本人修复，本条路径未改动 |
| 浏览器级渲染（jsdom + 真实后端） | **已验证** | F06：匿名访客在公开资料详情页拿到可点下载按钮，且真实下载到 30 字节。F05：已登录用户在"我的上传"第 1 页发布新资料后，列表**无整页刷新**即出现该资料（后端总数同步增长），11/11 PASS |
| 后端真实 HTTP 端到端 | **已验证** | 隔离实例（独立 SQLite 库与上传目录、空闲端口）上跑 18 项断言全部 PASS：配置与内建分类、访客列表、登录、presign、签名 PUT、创建、标签归一化、详情与改权、下载字节与原件逐字节相等、`nosniff`、下载去重、收藏计数、评分均分、改 private 后访客 404 且移出列表、未知资源 404 |
| 前端类型检查 | **已验证** | `pnpm typecheck` **exit 0 零报错**。注意：这是把 `samryetha-ui-commons` 与 `@lako/ui` 真正安装并构建出 `dist` 之后跑的结果，不是"缺包降级"的假通过 |
| i18n 中英对齐 | **已验证** | `I18nKey` 由 `en.ts` 推导、`zh-CN.ts` 声明为 `Record<I18nKey, string>`，类型检查通过即代表两语言键集完全一致 |
| 前端生产构建 | **仍未验证，但已不再是盲区** | 本机沙箱下 `vite build` 依旧不可运行（根因见下），因此**没有**产出生产包。但页面的真实渲染已由两条独立证据覆盖（见下两行），构建这一环仍建议在 CI 补跑一次 |
| SSR 渲染（服务端直出 HTML） | **已验证** | 绕开 Vite/esbuild：用 TypeScript 纯 JS 编译器 API 转译 54 个模块为 CommonJS，Node 内直接调用真实 SSR 入口。**14/14 PASS**，含 `href="/files"`、中文「文件服务」、英文「Files」、列表页骨架与控件、详情路由分发、以及既有路由（首页/反馈页）未被破坏 |
| 页面真实内容渲染（客户端） | **已验证** | jsdom 挂真实 DOM + 跑 useEffect + 向真实后端取数。**13/13 PASS**，断言列表表格出现真实资料标题、表头、分类 chip、下载/收藏操作、详情页标题与元信息块、评分控件、面包屑；并含 XSS 断言（说明里故意植入的 `<script>` 未成为活标签） |
| 构建不可运行的根因 | 已定位到调用级 | Vite/esbuild 以**带管道的子进程**启动原生 esbuild/rolldown 二进制，workspace-write 沙箱禁止带输出捕获启动子进程（实测 `spawn EPERM`）；更早一次失败是 esbuild 把 `@tailwindcss/oxide` 的原生 `.node` 当文本读（`stream did not contain valid UTF-8`）。两者都发生在**读取业务源码之前**，且 `vite.config.ts` 未被本次改动触碰，属既有环境限制 |
| 证明方法可复现 | 已落盘 | `.dsh-local/ssr-render.mjs`（SSR）与 `.dsh-local/client-render.mjs`（客户端渲染），均为本机专用、已被 `.gitignore` 排除，不进入特性提交 |

> 结论：功能逻辑层与表现层**现均已有实证**——后端 366 条单测 + 18 条真实 HTTP 端到端断言，
> 前端 SSR 14 条 + 客户端渲染 13 条。唯一仍然空白的是"生产打包"这一环，其失败原因属环境限制而非代码，
> 建议由 CI 补跑。

---

## 12. 纪律与流程约定（本次开发遵守）

1. 不直接改 `main` / `dev`，全部工作在 `feat/file-service-20261007`。
2. **不 push**，是否推远端由管理员拍板。
3. 不使用 `sandbox_permissions` / 不请求提权。
4. 代码注释中英双语、换行不空行；代码内不得出现 emoji。
5. 遵循仓库既有目录结构与命名（后端 service 用 `_underscore` 函数，路由放 `routers/`）。
6. 每期产物落盘且可单独验证；失败只重跑该期对应步骤。
