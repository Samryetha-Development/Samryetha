# 文件服务·元数据编辑（Stage 3）

File-service metadata editing (stage 3)

管理员需求（2026-10-12）：**管理员还要可以编辑文件标签、简介这类的。**

## 1. 零迁移

No migration

`file_resources` 的 `description_md` 与 `tags` 两列、以及
`PATCH /api/files/resources/{id}`（`ResourcePatchBody` 支持 `title` / `descriptionMarkdown` /
`tags` / `categoryId` / `visibility` / `status`）在本阶段开始之前**就已经存在**。本阶段因此
不动表、不写迁移、不改 OpenAPI 的请求/响应结构，只补三件事：审计留痕、前端编辑入口、用例。

The `description_md` and `tags` columns and the resource PATCH endpoint already existed before this
stage. Nothing about the table, the migration path or the request/response contract changes here;
this stage adds an audit trail, a front-end editing entry point and test coverage.

## 2. 权限

Authorisation

沿用既有口径，**不另发明一套**：

| 操作 | 能力 | 判定 |
|---|---|---|
| 编辑标签 / 简介（以及标题、分类、可见性、状态） | `FILE_UPDATE` | `is_active(actor) and (is_global_mod(actor) or actor.id == uploader_id)` |

即：**管理员可编辑任何资料**（这是管理员的硬要求），**文件 owner 可编辑自己的**。
前端同理——编辑入口只在详情接口返回的 `can.update` 为真时渲染，而那个字段就是后端 authz 的
`FILE_UPDATE`；前端隐藏只是界面整洁，后端仍会再判一次。

Global admins may edit any resource (the hard requirement) and the owner may edit their own. The
client follows the same rule: the entry point renders only when `can.update` is true, and that field
*is* the backend's `FILE_UPDATE`. Hiding is cosmetic; the backend decides again.

「不能借元数据接口越权」这一点没有特别设计——它本来就是同一条路径：非 owner 非管理员在
`assert_can` 处得到 403，资料一个字段都不会动（有用例钉住）。

There is no special design needed for "the metadata endpoint must not be a privilege-escalation
path": it is the same path. A non-owner non-admin is refused with 403 at `assert_can` and not a
single field moves (pinned by a case).

## 3. 校验与边界

Validation and boundaries

| 项 | 口径 | 越界的处理 |
|---|---|---|
| 标签个数 | 最多 8 个 | 多出的丢弃（保留前 8） |
| 单个标签长度 | 最多 24 字符 | 截断 |
| 标签去重 | 大小写归一为小写后去重，保留首次出现顺序 | — |
| 标签分隔符 | 半角逗号、全角逗号、顿号、分号（`[,，、;；]`） | 纯空白项丢弃，不产生空标签 |
| 简介长度 | 最多 20000 字符 | **422 拒绝，不静默截断**（截断会让用户以为整段都存进去了） |

以上全部集中在 `files/service.py` 的 `normalize_tags` 与 `ResourcePatchBody` 的字段约束里，
前端只做同一套分隔符的拆分，不做第二套规则。

All of it lives in `normalize_tags` and the `ResourcePatchBody` field constraints; the client only
splits on the same separators rather than inventing a second rule set.

## 4. 空值语义

Empty semantics

- **不传某个字段 = 不改**（`ResourcePatchBody` 以 `None` 表示"未提供"）。
- **显式传空 = 清空**：`tags: []` 清空标签，`descriptionMarkdown: ""` 清空简介。

两者不可混淆：只传 `descriptionMarkdown` 的人必须保住自己的标签，否则"只想改简介"会静默丢掉
标签。编辑弹窗每次都会把两个字段都发出去，所以在那里"留空即清空"，弹窗顶部有一行专门说明。

Omitting a field leaves it alone; an explicit empty value clears it. The two must not be conflated,
or somebody who only meant to edit the description would silently lose their tags. The dialog always
sends both fields, so there "empty means clear", and it says so at the top.

## 5. 并发

Concurrency

**后写覆盖**，没有乐观锁：`file_resources.version` 这一列本期恒为 1，不参与冲突检测。
两位编辑者同时保存时，后到的那次生效；这不是缺陷而是一个明确口径，因此有用例明确钉住它
（`test_last_write_wins_and_is_documented`），免得日后有人以为存在冲突检测、据此写出错误的界面逻辑。

Last write wins; there is no optimistic locking. The `version` column is always 1 for now and plays
no part in conflict detection. When two editors save at once the later one wins. That is a stated
policy rather than an oversight, so a case pins it down explicitly, lest somebody later assumes
conflict detection exists and builds interface logic on that assumption.

## 6. 审计

Audit trail

每一次**确实改变了内容**的编辑都会写一条 `moderation_actions`：

- `action`: `file.update_metadata`
- `target_type` / `target_id`: `file_resource` / 资料 id
- `actor_user_id`: 执行者
- `reason`: 只记**真正发生变化的字段的新旧值**，例如
  `title='旧'->'新'; tags=['a']->['b']; categoryId=1->2`

简介**只记长度**（`description=12chars->20chars`）：它上限 20000 字符，把新旧全文塞进 `reason`
会把审计行撑成一份内容副本，而审计行的用途是"谁在什么时候把什么改成了什么"；需要原文时读资料
本身即可。

**没有变化就不写审计**：否则每一次"打开编辑器再保存"都会在审计表里留下噪声，真正需要追溯的
改动反而被淹没（有用例钉住）。

A row is written only when something actually changed; otherwise every open-and-save would add
noise and bury the edits that matter. The description is recorded as a length only, because pasting
both 20,000-character versions would turn an audit row into a copy of the content.

## 7. 界面

Interface

详情页在 `can.update` 为真时显示「编辑资料信息」；弹窗内可改标签与简介，保存成功后用**响应体**
回填详情（后端会归一化：去重、小写、截断，用本地输入回填会显示出与服务端不一致的标签）。
保存失败显示服务端给出的具体原因，成功显示提示，二者都不静默。

The detail page shows "Edit details" when `can.update` is true. The dialog edits tags and the
description, and a successful save writes the *response* back into the detail, because the backend
normalises (de-duplicate, lower-case, truncate) and echoing the local input would show tags that
disagree with what was stored. Failures show the server's own reason and successes show a notice;
neither is silent.

标题、分类、可见性、状态同在 `PATCH` 的能力范围与同一条权限之下，但本阶段**刻意不做它们的界面**：
需求点名的是标签与简介，把其余字段一并搬上台面会扩大评审面与误操作面。后端能力原样保留。
Title, category, visibility and status sit under the same endpoint and the same permission, but this
stage deliberately ships no interface for them: the requirement names tags and the description, and
putting the rest on screen would widen both the review surface and the room for mistakes. The
backend ability is untouched.
