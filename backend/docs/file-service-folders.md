# 文件服务·文件夹分类（Stage 2）

File-service folders (stage 2)

本文记录文件夹功能的数据模型、迁移方案、权限口径与**删除策略**，并说明为什么这样选。
This document records the data model, the migration plan, the authorisation rules and, in
particular, the **delete policy** of the folder feature, along with the reasoning behind each.

## 1. 定位：文件夹是视图，不是权限边界

A folder is a view, never a permission boundary.

文件夹只决定"资料怎么摆"，绝不决定"谁能看"。可见性依然只看 `file_resources.visibility`：
`FileRepository._visible(viewer)` 在 SQL 层把受众收窄，文件夹过滤是叠加在其之上的**另一个**
`WHERE` 条件（`AND`，不是替代）。因此不存在"通过文件夹看到原本不可见的资料"这条路径。
A folder decides how resources are arranged and never who may see them. Visibility still comes from
`file_resources.visibility` alone: `FileRepository._visible(viewer)` narrows the audience at the SQL
layer and the folder filter is a further `WHERE` condition ANDed on top of it, not a replacement.
There is therefore no path by which a folder reveals a resource that would otherwise be invisible.

同理，文件夹上的 `resourceCount` 也是**按查看者可见性**算出来的，不是物理行数——否则那个数字
本身就会泄漏"这里还藏着多少份你看不到的资料"。
For the same reason a folder's `resourceCount` is computed per viewer rather than counted
physically; otherwise the number itself would leak how many resources are hidden from the viewer.

## 2. 数据模型

Data model

| 对象 | 位置 | 说明 |
|---|---|---|
| `file_folders` 表 | `backend/src/samryetha/core/schema.py` | `id` / `parent_id`（自引用，NULL = 顶层）/ `name` / `description` / `sort_order` / `deleted_at` / `deleted_by` / `created_at` / `updated_at`，索引 `file_folders_parent_sort_idx` |
| `file_resources.folder_id` 列 | 同上 | `INTEGER`，`ForeignKey("file_folders.id")`，**可空、无默认值**，索引 `file_resources_folder_created_idx(folder_id, created_at)` |
| 能力 `FILE_MANAGE_FOLDER` | `backend/src/samryetha/authz/service.py` | `is_global_mod`，与 `FILE_MANAGE_CATEGORY` 同档 |

嵌套深度上限 `MAX_FOLDER_DEPTH = 5`（`files/service.py`）。限制的理由不是技术性的：再深下去
导航就不再是"分类"而成了迷宫，面包屑也会挤掉列表本身的宽度。顶层文件夹算第 1 层。
Nesting is capped at `MAX_FOLDER_DEPTH = 5`. The reason is not technical: past that point the
navigation stops classifying and becomes a maze, and the breadcrumb eats the width the list needs.
A top-level folder counts as level 1.

## 3. 迁移方案（无迁移框架）

Migration plan (there is no migration framework)

本项目没有迁移框架，`core/schema.py` 是唯一真源，启动流程（`core/app.py`）先 `create_schema()`
再 `ensure_schema_drift()`。

1. **新增表 `file_folders`**：由 `create_schema()` 的 `create_all` 幂等建立，存量库无需手工操作。
   The new table is created idempotently by `create_schema()`, so an existing database needs no
   manual step.
2. **`file_resources` 新增列 `folder_id`**：由 `ensure_schema_drift()` 的
   `ALTER TABLE ... ADD COLUMN` 自动补上。该列**刻意可空且无默认值**，同时满足三件事：
   - 历史数据自动落在 `NULL` 上，也就是「未分类」，**零回填**；
   - 满足 `ensure_schema_drift` 的自动补列条件（`core/db.py`：非 PK、非 UNIQUE；`NOT NULL`
     列必须有默认值，否则存量行无法回填）；
   - SQLite 的 `ALTER TABLE ADD COLUMN` 在带 `REFERENCES` 时必须默认 `NULL`，此处正好满足。
   The column is auto-added by `ensure_schema_drift()`. It is deliberately nullable with no default,
   which satisfies three things at once: pre-existing rows land on `NULL`, i.e. "unfiled", with no
   backfill; the auto-add conditions are met (not PK, not UNIQUE; a `NOT NULL` column would need a
   default or existing rows could not be backfilled); and SQLite's `ALTER TABLE ADD COLUMN` requires
   a `NULL` default on a column carrying `REFERENCES`, which is exactly the case here.
3. **索引** `file_resources_folder_created_idx`：由 `ensure_schema_drift()` 的
   `CREATE INDEX IF NOT EXISTS` 补建（新增列往往配套新索引，而 `create_all` 对既有表是 no-op）。
   The index is created by the same idempotent path.
4. **向前兼容**：所有历史资料的 `folder_id` 为 `NULL`，在界面上即「未分类」。
   Forward compatibility: every pre-existing resource has a `NULL` folder id and therefore shows up
   under "Unfiled".
5. **回滚**：本功能只做加表与加列，回滚只需 `git revert` 对应提交。遗留的 `file_folders` 表与
   `folder_id` 列对旧代码完全无影响（旧代码的 `SELECT` 不引用它们）。若确需彻底清理，可在确认
   无代码引用后手工执行 `DROP TABLE file_folders`，以及 SQLite 3.35+ 的
   `ALTER TABLE file_resources DROP COLUMN folder_id`（注意：被外键定义引用的列**不能**直接
   DROP，实测报 `unknown column "folder_id" in foreign key definition`，需先重建该表）。
   Rollback is a plain `git revert`: the feature only adds a table and a column, both ignored by the
   old code because its `SELECT`s never reference them. Purging them entirely would need
   `DROP TABLE file_folders` plus a table rebuild for `folder_id`, since SQLite refuses to drop a
   column referenced by a foreign key definition (measured: `unknown column "folder_id" in foreign
   key definition`).
6. **数据破坏性**：整个迁移过程没有任何删除或改写既有数据的语句，因此不需要数据备份即可回滚。
   Nothing in the migration deletes or rewrites existing data, so a rollback needs no data backup.

## 4. 权限口径

Authorisation

| 操作 | 能力 | 判定 |
|---|---|---|
| 读文件夹列表 | 无（匿名可读） | 与读分类同口径：骨架对所有人可见，但计数按查看者过滤 |
| 新建 / 改名 / 移动 / 删除文件夹 | `FILE_MANAGE_FOLDER` | `is_global_mod(actor)` |
| 把资料放进文件夹 / 移出 | **既有** `FILE_UPDATE` | `is_active(actor) and (is_global_mod(actor) or actor.id == uploader_id)` |

归属变更刻意**不新增能力**：把资料归类是对某一份资料的写操作，凭什么能改别人的资料，就该凭什么
能改它的归属——另开一条更宽或更窄的路径都会让两处规则各说各话。
Filing deliberately introduces no new ability: filing a resource is a write to that resource, so
whatever lets you edit someone's resource is exactly what should let you move it. A separate path,
wider or narrower, would leave two rules disagreeing.

路由层只用 `require_active_user`（仅认证），"能不能做"一律在 service 层经 authz 判定——
沿用 `files/router.py` 顶部写明的既有约定：同一条规则不在路由与业务层各写一份。
The routes only authenticate; whether an actor may do something is decided in the service layer
through authz, following the convention stated at the top of `files/router.py`.

## 5. 删除策略（数据安全）

Delete policy (data safety)

**默认且唯一策略：删除文件夹时，其中的资料一律「移到未分类」（`folder_id` 置 `NULL`），
绝不递归删除任何资料。** 若该文件夹下还有子文件夹，删除**直接拒绝**（409），要求先处理它们。

**The one and only policy: deleting a folder moves its resources to "Unfiled" (`folder_id` set to
`NULL`) and never recursively deletes a single one.** If the folder still has subfolders the delete
is refused outright with 409.

依据：
- 资料是长期资产，文件夹只是视图。删一个视图不该带走它指向的东西。
- 仓库既有约定指向同一底线：分类在仍持有资料时**直接拒绝删除**
  （`files/service.py` 的 `delete_category`：`Category still holds resources`）。可见本项目对
  "删容器连带删内容"是零容忍的。文件夹策略在这条底线上略微放宽（允许删空文件夹，非空则把资料
  解绑），但底线完全相同：**任何情况下都不删资料**。
- 子文件夹直接拒绝的原因：删父级顺手把一整棵子树的结构打散，是用户看不见的破坏——资料还在，
  但它们赖以被找到的那条路径消失了。

Reasoning:
- A resource is a long-lived asset and a folder is only a view; deleting a view must not take away
  what it pointed at.
- The repository already draws the same line: a category that still holds resources **refuses** to
  be deleted (`files/service.py`, `delete_category`: `Category still holds resources`). Deleting a
  container together with its contents is plainly not acceptable here. The folder policy is slightly
  more permissive (an empty folder may go, a non-empty one detaches its resources) but the bottom
  line is identical: no resource is ever deleted.
- Subfolders are refused because deleting a parent quietly scatters a whole subtree's structure:
  the resources survive, but the path people used to find them does not.

删除的后果必须对用户可见：接口返回 `detachedResourceCount`，界面在确认删除之前就写明
"其 N 份资料将移到未分类，不会删除任何资料"。
The consequence has to be visible: the endpoint returns `detachedResourceCount` and the interface
spells out "its N resources will move to Unfiled; no resource is deleted" before the delete is even
confirmed.

建/改名/移动/删除文件夹，以及设置资料归属，都会写一条 `moderation_actions` 审计
（`file.folder.create` / `file.folder.update` / `file.folder.delete` / `file.resource.set_folder`，
`reason` 里记 `name`、`parent`、`fields`、`detachedResources` 与 `from=… to=…`）。
Every folder write and every membership change leaves a `moderation_actions` audit row.

## 6. 接口

API

| 方法与路径 | 说明 |
|---|---|
| `GET /api/files/folders` | 全部文件夹（扁平，前端按 `parentId` 拼树与面包屑） |
| `POST /api/files/folders` | 新建（`name` / `parentId?` / `description?` / `sortOrder?`） |
| `PATCH /api/files/folders/{id}` | 改名 / 改描述 / 改排序 / 改父级（`parentId` 缺席 = 不动，显式 `null` = 移到顶层，二者用 `model_fields_set` 区分） |
| `DELETE /api/files/folders/{id}` | 删除；资料移到未分类；有子文件夹时 409 |
| `PUT /api/files/resources/{id}/folder` | 设置资料归属；`folderId` 缺席或 `null` 都表示"移出到未分类" |
| `POST /api/files/resources` | 新增可选 `folderId`：上传时一次写定归属；缺省/`null` = 落在根目录（不属于任何文件夹）；文件夹不存在时 404，且不写任何行 |
| `GET /api/files/resources?folderId=` / `?unfiled=true` | 按文件夹过滤；两者都不传 = 全部（含已归类与未归类） |

`PUT .../folder` 刻意做成独立端点，而不是往 `PATCH /api/files/resources/{id}` 里塞一个
`folderId` 字段：PATCH 的既有约定是"字段为 `None` 即不改"，而这里 `null` 必须表示"移出到未分类"
这个真实动作。两种语义挤在同一个 `None` 上，早晚会把某个人的资料静默移出文件夹。
The membership endpoint is deliberately separate rather than a `folderId` field on the resource
PATCH: PATCH treats a `None` field as "leave it alone", while here `null` must mean the real action
"move to unfiled". Squeezing both meanings onto one `None` would eventually move somebody's
resources out of a folder without them asking.

## 7. 界面

Interface

按文件管理器（Windows 资源管理器 / 网盘）的语义组织，视图分三种位置：

| 视图 | 列表内容 | 取数参数 |
|---|---|---|
| **根目录**（默认） | 顶层文件夹行 + 不属于任何文件夹的资料 | `unfiled=true` |
| **某个文件夹** | 该文件夹的直接子文件夹行 + 它自己的资料 | `folderId=<id>` |
| **全部资料（含文件夹内）** | 跨文件夹的平铺视图，仅供搜索/管理时显式选择 | 两个都不传 |

- 默认视图是**根目录**，不是"全部"。旧版默认"全部"（不带任何文件夹参数）会把已归档与未归档的
  资料混在一起，于是"移入文件夹之后那一行还在外面显示"，看起来像移动没生效。
- 位置条**常驻**：任何标签、任何层级都显示「当前位置 + 根目录 + 返回上一级」；有子级时列表本身
  还带一条「返回上一级」行。常驻是必要的：非管理员在根目录下如果没有任何内容，整个文件夹区块
  会被 `.files-folders:empty` 隐藏，"文件夹功能存在"就完全不可见。
- 「全部资料（含文件夹内）」必须显式选择，并在位置条上单独标出——它算不上一路径节点，
  也不该是默认落点。
- 行内「移动到文件夹」是**有边框/底色/图标/展开箭头的按钮**，并显示当前归属；移动成功会给出
  「已移动到：…」的反馈，失败显示服务端原因（不静默）。
- 上传弹窗有**目标文件夹**选择，默认就是当前所在文件夹（在文件夹里上传 = 归入该文件夹），
  发布后视图落到目标文件夹上，而不是切到按 viewer 取数的「我的上传」。
- 文件夹导航条挂在所有标签下；管理动作（新建/改名/删除）只在「全部资料」标签出现；在个人标签
  上点文件夹会先切回该标签，保证这次点击一定有可见结果。
- 删除前必须显式确认并写明后果（资料移回根目录、一份都不删）；建立/改名/删除失败一律在界面上
  显示服务端给出的原因。

## 8. 取舍与边界

Trade-offs and boundaries

- 「全部资料（含文件夹内）」保留但不再是默认：跨文件夹搜索与管理确实需要它，而它作为默认视图
  正是本缺陷的来源。它被放在下拉框末尾并在位置条上单独标注，不会与"根目录"混淆。
- 列表行内的选择器已经足够明确，因此 `POST /api/files/resources` 直接接受 `folderId`：
  先建在根目录再移动会留下"创建成功但归类失败"的部分状态，用户看到"文件传上去了但没进文件夹"
  却难以判断。上传时一次写定归属把这种部分状态彻底去掉。
- 拖拽上传与弹窗上传同一语义：都落在**当前所在文件夹**里（在文件夹里拖入 = 归入该文件夹）；
  在根目录下则落在根目录。


