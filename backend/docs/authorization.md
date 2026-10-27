# Samryetha 授权模型

## 唯一入口

全站授权收敛到单一函数（`src/authz/can.ts`）：

```ts
can(actor, ability, resource, ctx): Promise<boolean>
```

- `actor`：`SessionUser | null`（null = guest）。
- `ability`：能力常量（`Abilities`），如 `discussion.create`。
- `resource`：目标领域对象（board / discussion / reply / user / attachment / null）。
- `ctx`：`{ db }`（授权可能内联查库，如板块成员）。
- 业务模块用 `assertCan(...)`，失败抛 `FORBIDDEN`。

**约束：业务代码零 `user.role ===` 判断。** 所有授权一律经 `can()`。

## 角色层级

```
admin ⊃ board-mod ⊃ student ⊃ guest
```

- `board-mod` 权限来自 `board_members.role = "moderator"`，在 `can()` 内联查库。
- 全局角色只有 `admin`：`users.role` 上的 `moderator` 是历史遗留值，**今天不带来任何全局特权**
  （`is_global_mod` 只认 `admin`）。
- 历史遗留的 `users.role = "moderator"` 不再由服务启动自动改写：启动只打一条 WARNING（数量 + 用户 id），
  改角色走 `backend/scripts/migrate_moderator_roles.py`（默认 dry-run，`--apply` 才写，并写一条
  `moderation_actions` 审计行 `user.role.moderator_to_admin`）。见 `backend/src/samryetha/auth/moderator_roles.py`。

- `board-mod` rights come from `board_members.role = "moderator"`, looked up inline inside `can()`.
- The only global role is `admin`: a legacy `moderator` value on `users.role` grants **no global
  privilege today** (`is_global_mod` only accepts `admin`).
- A legacy `users.role = "moderator"` is no longer rewritten at startup: startup only emits one WARNING
  (count + user ids), and the role change goes through `backend/scripts/migrate_moderator_roles.py`
  (dry-run by default; `--apply` writes, together with a `moderation_actions` audit row
  `user.role.moderator_to_admin`). See `backend/src/samryetha/auth/moderator_roles.py`.

## 能力表

| 能力 | 允许者 | 资源 |
|------|--------|------|
| `board.create` | admin | null |
| `board.delete` | admin | null |
| `board.update` | admin / board-mod | board |
| `board.manage_members` | admin / board-mod | board |
| `board.join` | active 用户，且满足 visibility 约束 | board |
| `discussion.create` | active 用户，按 board.posting_policy（everyone=全员 / members=成员 / moderators=全局mod+board-mod） | board |
| `discussion.read` | 按 board.visibility（public=全员 / members=成员 / private=成员） | board |
| `discussion.update` | 作者本人（未删）或全局 mod | discussion |
| `discussion.delete` | 作者本人（未删）或全局 mod | discussion |
| `discussion.pin` / `discussion.lock` | 全局 mod / board-mod | discussion |
| `reply.create` | active 用户，讨论未锁未删，按 board 规则 | discussion |
| `reply.update` / `reply.delete` | 作者本人（未删）或全局 mod | reply |
| `user.update.self` | 本人 | user |
| `user.follow` | active 用户 | user |
| `report.create` | active 用户 | null |
| `moderation.view` | 全局 mod / admin | null |
| `moderation.resolve` | 全局 mod / admin | null |
| `user.ban` | 全局 mod / admin | null |
| `moderation.unban` | **admin only** | null |
| `attachment.create` / `attachment.delete` | active 用户（本人） | attachment |
| `presence.heartbeat` | active 用户 | null |
| `file.read` | `is_public=1`（匿名可读，与 visibility 正交）或按 `file_resources.visibility`（public=全员 / members=active 用户 / private=上传者本人或全局 mod） | fileResource |
| `file.create`（上传字节、新建资料） | **admin only**（2026-10-08 管理员裁定；此前是 active 用户） | null |
| `file.interact`（评分、收藏等普通互动） | active 用户 | null |
| `file.update` / `file.delete` | 上传者本人或全局 mod | fileResource |
| `file.category.manage` | 全局 mod / admin | null |
| `file.folder.manage`（建/改名/删文件夹） | 全局 mod / admin | null |
| `file.set_public`（Open to Public 直链公开开关） | **admin only**（与 visibility 的 owner 可改刻意分开） | null |
| `feedback.view` | active 用户，且为项目成员（admin 全通过） | feedbackProject |
| `feedback.create` | active 用户，且为项目成员（admin 全通过） | feedbackProject |
| `feedback.update` / `feedback.delete` | admin / 作者本人 / 该项目程序员 | feedbackItem |
| `feedback.manage`（标完成/过期/恢复） | admin / 该项目程序员 | feedbackItem |
| `feedback.project.manage`（建/改/删项目、派成员） | **admin only** | null |

> 反馈的"程序员"来自 `feedback_project_members.is_programmer`，在 `can()` 内联查库，独立于论坛版主体系。

> 文件服务的 `file.create` 与 `file.interact` **必须保持分开**：评分与收藏此前借用 `file.create` 做校验，
> 2026-10-08 把上传收口为管理员时，若沿用同一个能力，普通用户会连评分和收藏一起失去（管理员明确要求保留评分）。
> 收口后 router 层只依赖 `require_active_user`（认证），授权一律由 service 层经 `assert_can` 判定 —— 路由不散落
> `role == "admin"` 判断。

> **附件转入（`POST /api/files/resources/from-attachment`）的 owner 口径**：该动作仅全局管理员可发起
> （`file.promote_from_attachment`），但**结果资料的 `uploader_id` 取源附件的原上传者**，不是执行转入的管理员。
> 附件不按上传者过滤，普通用户上传的附件同样可被转入，因此「上传者本人」这一档在他身上必须成立：他能看到
> 「我的上传」条目，并持有 `file.update` / `file.delete`。**边缘口径**：原上传者已封禁或已注销（`users.status != "active"`
> 或 `users.deleted_at` 非空）时不指派给他 —— 该账号无法登录，指派会让资料成为无人可管的孤儿；此时 owner 回退为
> 执行转入的管理员，而管理员本就经 `is_global_mod` 对所有资料持有写权限，故不会产出无人可管的资料。每次转入都写
> 一条 `moderation_actions` 审计行（`action = file.promote_from_attachment`），同时记录发起人（`actor_user_id`）
> 与结果 owner（写在 `reason` 的 `owner=` 字段里），两者可以不同。

## 任务（例外：直接角色守卫）

任务看板 `/api/tasks`（含嵌套评论）**仅管理员可读写**，授权不经 `can()`，而是在路由上直接依赖 `deps.require_admin`。非管理员一律 403（未登录 401）。任务数据独立于 feedback，不参与板块/版主体系。

## 关键实现细节

- **封禁优先**：`actor.status === "banned"` 时所有能力直接返回 `false`。
- **论坛可见性**：`discussion.read` 基于 board 的 visibility 判定（帖子本身不做私有）。
- **soft-delete 交互**：已删讨论不可 update；删除动作本身要求"未删"状态。
- 授权查询所需的 DB 上下文通过 `AuthzCtx` 注入，避免模块间循环依赖。

## 测试覆盖

定时帖子接口要求 active 用户，并始终按作者 ID 限定所有权（管理员无跨账号特权）。创建及到期发布都检查 `discussion.create`，到期时读取当前角色、账号状态及板块策略。`can_modify=false` 的任务禁止改时和取消；关联草稿的读写/删除/立即发布以及关联附件删除均拒绝，防止绕过锁定。取消/改时与后台发布串行化，发布后返回 409。覆盖见 `tests/test_scheduled_posts.py`。

`tests/integration.test.ts` 的「can() 能力矩阵」覆盖：学生发帖 everyone/members 板、板块成员发帖、作者本人删帖、admin 任意删帖、mod/admin 治理权限、仅 admin 解封、banned 用户全禁、guest 不可发帖。
