# 附件转入文件服务（管理员专属）设计说明

- 分支：`feat/attachment-to-files-20261008`（基于 `origin/dev` 的 `ec6ffa0`）
- 目标：把论坛里已有的「附件」转成文件服务仓库内的「文件资料」
- 前置阅读：`docs/file-service/00-plan.md`、`backend/src/samryetha/files/service.py`、
  `backend/src/samryetha/authz/service.py`、`backend/src/samryetha/adapters/storage.py`

本文档记录动手前的代码调研结论，以及接口、鉴权、幂等、可见性/审核口径、
前端入口位置与可见性判断依据。

---

## 1. 调研结论（动手前读代码的结论）

### 1.1 两套「文件」的分工

| 维度 | 附件 `attachments` | 资料 `file_resources` |
| --- | --- | --- |
| 归属 | 依附讨论帖（`discussion_id`），未挂帖时是上传草稿 | 独立一等内容 |
| 可见性 | 由父帖推断（`AttachmentService.downloadable` 现算父帖 + 板块） | 自身 `visibility` 字段 |
| 生命周期 | 会被 `reap_orphans` 回收 | 长期资产，只软删 |
| 字节 | 同一套 `adapters/storage.py` 本地磁盘对象存储 | 同一套 |

结论：两者**共用同一个对象存储根与同一套 objectKey 规则**
（`OBJECT_KEY_RE = ^[0-9a-f-]{36}/[^/]{1,255}$`），因此「认领」在字节层面是零拷贝的。

### 1.2 授权入口只有一个

`authz/service.py` 的 `AuthorizationService.can/assert_can` 是唯一授权入口，
能力用 `Abilities.*` 字符串常量表达；仓库约定「业务代码不散落 `user.role == ...` 判断」。
现有 `FILE_*` 能力：`file.read / file.create / file.update / file.delete / file.category.manage`。
`is_global_mod(actor)` 即 `actor.role == "admin"`（`moderator` 已并入 `admin`）。

### 1.3 内容审核已经整体退役（关键）

- `backend/src/samryetha/core/db.py` 的 `retire_content_review()`：
  一次性标记 `content_review_removed_v1`，把 `discussions/replies/direct_messages/file_resources`
  的 `moderation_status` 全部置为 `approved`，并释放历史上被审核拦住的附件。
- `create_resource()` 里**没有任何审核闸门**：校验通过即写 `status="published"`。
- 因此「审核口径一致」在本仓库的当前语义下等于：**新入口同样不引入审核闸门，直接 `published`**。

### 1.4 文件服务既有的写入口约定

- `POST /api/files/resources`（`create_resource`）：
  - 走 `assert_actor_current` + `assert_can(FILE_CREATE)`（任何 active 用户都可）；
  - `visibility` 取值必须是 `VISIBILITIES = (public, members, private)`，**默认 `members`**；
  - `status` 恒为 `published`；`mime_type` 用 `content_type_for_object_key()` 按扩展名推导，
    绝不信任客户端声明（防存储型 XSS）；
  - 走 `storage.sign_path()` 的 HMAC presign 三段式（presign → signed PUT → signed GET）。
- 读接口不可见一律 404，不泄漏存在性（`files/router.py` 顶部约定）。

---

## 2. 接口设计

### 2.1 端点

```
POST /api/files/resources/from-attachment      （管理员专属）
```

之所以选 `/resources/from-attachment` 而不是 `/promote`：仓库既有风格是
`/api/files/resources`（集合）+ `/api/files/resources/{id}`（单体），
本端点是「以附件为输入创建一条资料」，挂在 `resources` 集合下的子路径最贴合。

### 2.2 请求体 `FilePromoteFromAttachmentBody`

| 字段 | 类型 | 必填 | 缺省 | 说明 |
| --- | --- | --- | --- | --- |
| `attachmentId` | int ≥ 1 | 是 | — | 被转换附件的标识 |
| `categoryId` | int ≥ 1 | 是 | — | 目标分类（必须存在且未软删） |
| `title` | str 1..160 | 否 | 从附件文件名推导 | 去掉扩展名的文件名；为空则退化为完整文件名 |
| `descriptionMarkdown` | str ≤ 20000 | 否 | `""` | 资料描述 |
| `tags` | list[str] \| str | 否 | `[]` | 走既有 `normalize_tags()` |
| `visibility` | enum(public/members/private) | 否 | `members` | 与 `create_resource` 同缺省 |

### 2.3 响应体

复用既有 `FileResourceDetail`（201），与 `POST /api/files/resources` 完全同构，
前端因此可以复用同一个列表项渲染与「进列表」逻辑。

### 2.4 状态码

| 情形 | 状态码 | 响应体 |
| --- | --- | --- |
| 成功 | 201 | `FileResourceDetail` |
| 未登录 | 401 | 统一错误包络 |
| 已登录但非 admin | 403 | 统一错误包络（`Admin access required`） |
| 附件不存在 / 未挂帖（pending、uploaded）/ 父帖或板块已撤下（orphaned）/ 磁盘无字节 | 404 | 统一包络（`Attachment not found`，刻意不区分） |
| 分类不存在 | 404 | 统一包络 |
| `visibility` 非法或超出源附件受众上限 | 400 | 统一包络 |
| 同一附件重复转换 | 409 | 统一包络 |
| 磁盘字节与记录体积不一致 | 409 | 统一包络 |

**不泄漏约定**：非管理员在 body 校验之前就被 `require_admin` 拦下
（FastAPI 先解子依赖、后解析 body），因此非管理员永远看不到任何与附件是否存在有关的响应，
也不可能通过 422/404 的差异探测附件 id。

---

## 3. 鉴权设计

1. **新能力**：`Abilities.FILE_PROMOTE_FROM_ATTACHMENT = "file.promote_from_attachment"`，
   在 `AuthorizationService.can()` 的「不依赖 resource 的能力」分支里判定
   `is_global_mod(actor)` —— **只给 admin**。
2. **service 层**：`FileService.promote_from_attachment()` 里
   `assert_actor_current()` + `assert_can(FILE_PROMOTE_FROM_ATTACHMENT)`，
   与既有写入口一致；且**鉴权先于一切资源查找**（顺序：鉴权 → 找附件 → 找分类 → 校验参数）。
3. **router 层**：按仓库既有风格用 `Depends(require_admin)` 再兜一道
   （与 `POST /api/files/categories` 等管理端点一致）。
4. 全流程**不出现** `user.role == "admin"` 之类的散落判断。

---

## 4. 字节归属：认领（claim），不复制

新资料的 `object_key` **直接复用源附件的 object_key**，字节零拷贝。

理由：
- 附件与资料共用同一存储根与同一套 objectKey 规则，「认领」天然可行；
- 零拷贝意味着不存在「复制到一半失败留下半个对象」的中间态；
- 转换后两条下载链路同时可用且指向同一份字节：
  - `/api/attachments/serve/{object_key}`（附件链路，签名 + 父帖可见性复核）
  - `/api/files/serve/{object_key}`（资料链路，签名 + 资料可见性复核）
- 幂等性可以白拿（见 §5）。

### 4.1 认领带来的三个回收/删除风险与护栏

认领后对象被两个子系统引用，必须防止任一子系统把它回收掉：

1. **附件孤儿回收（撤下的父帖）**：`discussions/repository.py:486` 在删除讨论帖时把其附件置为
   `orphaned`，而 `AttachmentService.reap_orphans()` 会回收 `orphaned` / `pending` /
   「已上传但未挂帖」的附件对象。**这是最危险的路径**：先转入资料、再删掉源讨论帖，
   附件就变成回收候选，字节会被删掉而资料行还在。
   → 护栏：`AttachmentRepository.orphan_candidates()` 增加
   「该 object_key 未被 `file_resources` 认领」条件（`_unclaimed_by_resource()`）。
2. **附件删除**：`DELETE /api/attachments/{id}` 是**硬删**（删行 + 删对象）。
   → 护栏：`AttachmentService.delete()` 在对象已被资料认领时拒绝删除（409），
   提示先处理对应资料。
3. **文件服务自己的孤儿对象回收**：`FileService.reap_orphan_objects()`
   （PR #85 引入）按 `claimed_keys() = attachments ∪ file_resources` 的引用差集回收。
   → 无需改动：认领后 object_key 同时出现在两张表里，本就不可能进入差集。
   （口径一致性也因此天然成立：附件侧的护栏用的是同一个「资料是否引用」判据。）

（前两条护栏是本功能的必要组成：没有它们，「转换后下载链路不能坏」不成立。）

**关于「软删除源附件」**：本仓库的附件**没有软删除**——
`attachments` 表没有 `deleted_at`，删除 = 删行 + 删对象。
因此不存在「附件软删后资料仍可下载」这一测试场景；等价的真实场景是
「源讨论帖被删除（附件行保留、state 变为 orphaned）」与
「有人尝试硬删附件（被 409 拦下）」，两者都各有用例覆盖。

---

## 5. 幂等：确定的「拒绝」语义

**同一附件重复转换 = 409，明确报错。** 不做静默复用。

实现：`file_resources.object_key` 上有 `UNIQUE` 约束，而认领复用了附件的 object_key，
所以第二次转换必然命中唯一约束。服务层先做一次显式 `is_object_claimed()` 判定，
给出干净的 409；并发窗口由 `IntegrityError → 409` 兜底（与既有 `create_resource` 同款处理）。

需要说明的边界（已知且刻意保留）：
- 资料是**软删除**（`deleted_at`），软删后行仍在，唯一键仍被占用，
  因此「删除资料后再重新转换同一附件」同样是 409。此时应当恢复/编辑原资料，
  或对该附件换用其它 object_key（本功能不支持，属于刻意收紧）。

---

## 6. 可见性与审核口径的一致性结论

### 6.1 审核口径（一致）

内容审核已整体退役（§1.3），`create_resource` 无审核闸门、直接 `published`。
本入口**同样不引入审核闸门**，新资料 `status = "published"`。
两者口径完全一致，不存在「绕过审核」的可能——因为已经不存在审核。

### 6.2 可见性口径（一致 + 额外只收紧不放大）

与 `create_resource` 一致的部分：
- 复用同一组 `VISIBILITIES` 白名单与同一默认值 `members`；
- 不新增任何可见性取值；
- 非法取值一律 400。

在其之上**额外增加一条收紧规则**（本入口比 `create_resource` 更严，不是更松）：

> 新资料的可见性不得超过源附件的受众上限。上限由源附件当前的真实受众推导：
> - 父帖所在板块 `visibility == "public"` → 上限 `public`
> - 其余（板块 `members` / `private`）→ 上限 `private`

理由：附件的可见性由父帖推断，而**版内可见**这一档在文件服务里并不存在
（文件服务只有 public「所有人」/ members「登录用户」/ private「上传者与管理员」）。
若允许把「仅版内可见」的附件转成 `members`，就等于把受众从「版成员」放大到「全体登录用户」，
这正是需求里要禁止的「通过转换绕过可见性」。因此非公开来源一律收敛到 `private`。

- 缺省 `visibility` 为 `members`，但会被**向下夹紧到上限**（公开来源 → `members`；非公开来源 → `private`），
  缺省调用永远成功（不因缺省值报错）。
- **显式**请求超过上限时**报 400**（不静默改写）：明确告知调用方被拒，避免出现「我明明传了 public」的错觉。
- 事后确实需要放宽可见性时，走**显式且可审计**的既有入口
  `PATCH /api/files/resources/{id}`（要求 `FILE_UPDATE`）——那是一个独立动作，不是转换的副作用。

### 6.3 可转换的前提（同样服务于「不绕过可见性」）

只有 `state == "attached"` 且父帖/板块仍存活的附件才能被转换：
- `pending` / 已上传但未挂帖的附件是用户的上传草稿，尚未对外发布；
  管理员把它转成资料等于替用户做发布决定 → 拒绝。
- 父帖或板块已删除、附件已被标记 `orphaned` → 内容已被撤下，
  不允许通过转换路径复活。
- 这两类一律按 **404 "Attachment not found"** 处理（与「不可见一律 404」的口径一致，
  同时也让「不存在」与「存在但不可转换」在响应上不可区分）。

### 6.4 新资料的归属

`uploader_id` = 执行转换的管理员（= 操作者），与 `create_resource` 的
「uploader = 创建者」语义一致；同时也符合 `file.update/delete` 只认
「uploader 或 admin」的既有授权模型。

---

## 7. 前端入口

### 7.1 位置选择

| 候选位置 | 评价 |
| --- | --- |
| 文件服务列表页 `/files` 的管理区 | 需要先选附件，但列表页没有任何附件上下文，得再造一个附件选择器（新查询接口 + 新 UI），成本与风险都高 |
| 附件详情 | 本仓库**没有**独立的附件详情页（`GET /api/attachments/{id}` 只有接口，前端无路由） |
| **附件展示处（讨论帖详情页的附件区）** | **附件就在眼前，上下文天然完整；且这正是「把论坛里已有的附件转成资料」的真实操作场景** |

因此选择：**讨论帖详情页的附件区**（`frontend/src/thread-page.tsx`
→ `frontend/src/attachment-list.tsx`，即本仓库唯一渲染附件列表的组件）。

### 7.2 可见性判断依据（复用既有身份来源，不自造一套）

- 身份来源：`useAuth()`（`frontend/src/lib/auth.tsx`），即后端 `GET /api/auth/me` 的
  `UserResponse`，`user.role` 是权威角色；
- 管理员判定沿用仓库既有写法 `user?.role === "admin"`
  （同 `thread-page.tsx:177` 的 `isStaff`、`tasks-page.tsx:58` 的 `isAdmin`）；
- 非管理员：**不渲染**该按钮/表单项（不是 disabled、不是 CSS 隐藏），
  DOM 里根本不存在，普通用户无从发现。

### 7.3 交互与「刷新进列表」

1. 管理员在某个附件行上点击「转入文件服务」→ 打开小表单（选分类、填标题、可选可见性）；
2. 提交成功后：
   - 表单关闭并给出**可见反馈**（成功条：新资料标题 + 指向 `/files/{id}` 的链接）；
   - 该附件行立刻显示「已转入文件服务」徽标 + 直达新资料的链接
     —— **这就是「新资料刷新进列表」在本入口的落地**：入口在附件区，被刷新的列表就是附件区这一行，
     不再出现「成功后界面毫无变化」的 F05 类问题；
   - 失败（含 403/404/409）给出可见错误文案（403/401 提示无权限/未登录，409 提示已转入或不可转换）。
3. 文件服务列表页 `/files` 自身在挂载时取数（`FilesPage` 的 `loadList` effect），
   并在 `onPublished` 后用 `refreshToken` 强制重取；本入口不引入第二套缓存，
   因此从附件区跳过去时看到的一定是最新数据。

---

## 8. 未验证项

- 前端生产构建（`pnpm build`）未在本机复现：本机沙箱下 esbuild 读取
  `@tailwindcss/oxide` 原生 `.node` 报 UTF-8、随后 externalize 触发 spawn EPERM。
  改用 `ts.transpileModule` → CommonJS → 进程内 SSR `render()` 做页面级验证。
  生产构建由 CI 覆盖。
