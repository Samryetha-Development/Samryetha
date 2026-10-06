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
admin ⊃ moderator ⊃ board-mod ⊃ student ⊃ guest
```

- `board-mod` 权限来自 `board_members.role = "moderator"`，在 `can()` 内联查库。
- `admin` / `moderator` 为全局角色（`users.role`）。

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
| `feedback.view` | active 用户，且为项目成员（admin 全通过） | feedbackProject |
| `feedback.create` | active 用户，且为项目成员（admin 全通过） | feedbackProject |
| `feedback.update` / `feedback.delete` | admin / 作者本人 / 该项目程序员 | feedbackItem |
| `feedback.manage`（标完成/过期/恢复） | admin / 该项目程序员 | feedbackItem |
| `feedback.project.manage`（建/改/删项目、派成员） | **admin only** | null |

> 反馈的"程序员"来自 `feedback_project_members.is_programmer`，在 `can()` 内联查库，独立于论坛版主体系。

## 任务（例外：直接角色守卫）

任务看板 `/api/tasks`（含嵌套评论）**仅管理员可读写**，授权不经 `can()`，而是在路由上直接依赖 `deps.require_admin`。非管理员一律 403（未登录 401）。任务数据独立于 feedback，不参与板块/版主体系。

## 关键实现细节

- **封禁优先**：`actor.status === "banned"` 时所有能力直接返回 `false`。
- **论坛可见性**：`discussion.read` 基于 board 的 visibility 判定（帖子本身不做私有）。
- **soft-delete 交互**：已删讨论不可 update；删除动作本身要求"未删"状态。
- 授权查询所需的 DB 上下文通过 `AuthzCtx` 注入，避免模块间循环依赖。

## 测试覆盖

`tests/integration.test.ts` 的「can() 能力矩阵」覆盖：学生发帖 everyone/members 板、板块成员发帖、作者本人删帖、admin 任意删帖、mod/admin 治理权限、仅 admin 解封、banned 用户全禁、guest 不可发帖。
