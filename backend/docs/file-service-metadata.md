# 文件服务·元数据编辑（Stage 3 起，含 2026-10-14 的界面与审计补齐）

File-service metadata editing (from stage 3, including the interface and audit work of 2026-10-14)

管理员需求（2026-10-12）：**管理员还要可以编辑文件标签、简介这类的。**
2026-10-14 追加：验收发现"只改状态/只改可见性不写审计""删除零审计""弹窗不回填服务端归一化结果"
"未知取值被伪装成合法状态"等问题，本文档随之更新（见 §5 并发、§6 审计、§7 界面）。

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

**编辑界面上的提示不是乐观锁**（2026-10-14 补）：编辑弹窗每次都会把**全量字段**发出去，所以一个
陈旧的弹窗不止覆盖标签与简介，还会把可见性与发布状态一起盖回去（别人刚下架的资料会被静默重新发布）。
界面因此在保存前先取一次服务端现值比对，一旦发现字段在弹窗打开之后被改过，就**停下并说明对方改了
哪些字段**，由用户在"仍然保存"与"放弃我的改动并重新载入"之间选择。三条边界写清楚：

- 这是**客户端保护**，不是冲突检测：服务端不校验版本、也不拒绝写入，"后写覆盖"的口径一字未改。
- 用户确认"仍然保存"之后，写入的仍是表单里的值——覆盖依然是允许的，只是不再**静默**。
- 代价是每次保存多一次详情请求。取舍理由：被静默覆盖的是"谁能看到 / 是否出现在列表里"这一层，
  相对一次读取，代价可接受。

The notice in the editor is *not* optimistic locking (added 2026-10-14). The dialog sends every field on
every save, so a stale one overwrites not just tags and description but visibility and publication
status too - a resource somebody else just archived would be silently republished. The client therefore
reads the server's current values before saving and, when a field has moved since the dialog opened,
stops and names the fields that changed, leaving the user to choose between saving anyway and
discarding their edits. Three boundaries, stated plainly: it is a *client-side* guard and not conflict
detection (the server checks no version and refuses nothing, so last-write-wins is untouched); the
values written after "save anyway" are still the form's, so overwriting remains allowed and merely
ceases to be *silent*; and the cost is one extra detail request per save, accepted because what would
otherwise be overwritten silently is the layer that decides who can see the resource and whether it
appears in the list at all.

## 6. 审计

Audit trail

每一次**确实改变了内容**的编辑都会写一条 `moderation_actions`：

- `action`: `file.update_metadata`
- `target_type` / `target_id`: `file_resource` / 资料 id
- `actor_user_id`: 执行者
- `reason`: 只记**真正发生变化的字段的新旧值**，例如
  `title='旧'->'新'; tags=['a']->['b']; categoryId=1->2`

**六个字段一个不少**（2026-10-14 补）：`title` / `categoryId` / `tags` / `descriptionMarkdown` /
`visibility` / `status` 都参与"是否真的变了"的判定，`visibility` 与 `status` 记作
`visibility='members'->'private'`、`status='published'->'archived'`。修之前这两个字段只被写进
更新值、却没进变化清单，于是"只改状态"或"只改可见性"的编辑**一条审计都不写**——而下架恰好是
最需要追溯的操作（资料从列表里消失，事后查不出是谁做的）。

All six fields count (added 2026-10-14): title, category, tags, description, visibility and status all
take part in deciding whether anything really changed, with visibility and status recorded as
`visibility='members'->'private'` and `status='published'->'archived'`. Before the fix those two fields
were written into the update without joining the change list, so a status-only or visibility-only edit
wrote no audit row at all - and archiving is precisely the operation that most needs tracing, since the
resource then disappears from the list and "who did that" becomes unanswerable.

**删除也留痕**（2026-10-14 补）：`DELETE /api/files/resources/{id}` 成功时写一条
`action=file.resource.delete`（同样带 `target_type`/`target_id`/`actor_user_id`，`reason` 记被删资料
的标题）。软删除会同时挡掉详情页与下载，资料从界面上彻底消失，此前这个写操作却是全文件唯一没有
`insert_action` 的一处，删除之后审计表一行都不加。

Deletion is recorded too (added 2026-10-14): a successful `DELETE /api/files/resources/{id}` writes
`action=file.resource.delete`, with the same target/actor fields and the deleted resource's title in
`reason`. A soft delete takes out the detail page and the download and removes the resource from the
interface entirely, yet this write used to be the only one in the file with no `insert_action` call, so
a deletion added no row at all.

简介**只记长度**（`description=12chars->20chars`）：它上限 20000 字符，把新旧全文塞进 `reason`
会把审计行撑成一份内容副本，而审计行的用途是"谁在什么时候把什么改成了什么"；需要原文时读资料
本身即可。

**没有变化就不写审计**：否则每一次"打开编辑器再保存"都会在审计表里留下噪声，真正需要追溯的
改动反而被淹没（有用例钉住）。

A row is written only when something actually changed; otherwise every open-and-save would add
noise and bury the edits that matter. The description is recorded as a length only, because pasting
both 20,000-character versions would turn an audit row into a copy of the content.

**审计写在鉴权与存在性检查之后，且与写入同属一个事务**：失败或越权的请求不留痕。注意本仓库的
请求事务在异常时回滚（`core/db.py` 的 `request_conn`），所以"把审计挪到鉴权之前"这一类顺序错误
在数据层**观察不到**，用例也不声称覆盖它。

The audit row is written after authorisation and the existence check, inside the same transaction as the
write, so a failed or unauthorised request leaves nothing behind. Note that this repository's request
transaction rolls back on exception (`request_conn` in `core/db.py`), which makes an ordering mistake
such as "the audit before the authorisation check" unobservable at the data layer; the cases do not claim
to cover it.

## 7. 界面

Interface

2026-10-13/14 起，界面与后端能力对齐（本段此前写着"刻意不做标题/分类/可见性/状态的界面"，那句
话与实现不符，已删除）：

- **列表行内的编辑入口**：`files-page.tsx` 的行内操作组里「编辑」与「下载 / 收藏 / 删除」并列。
  入口只在 `canFileResource(item)`（= 后端 `FILE_UPDATE`）为真时渲染；点击时先按 id 取详情再开弹窗。
- **编辑弹窗六字段**：文件名、分区（分类）、标签、简介、可见性、状态。分区与可见性/状态的选项来自
  `/api/files/config`，**不手抄后端白名单**。简介与标签的空值语义是"清空"，其余字段每次全量发送。
- **保存后回填服务端结果**：弹窗显示的是**真正落库的那一份**（标题去首尾空白，标签小写去重、
  只保留前 8 个、单个截断 24 字符），不是用户输入的原文；父组件换了一份新快照时整体覆盖，同一份
  内容的重渲染不覆盖用户正在编辑的输入。
- **未知取值不伪装**：`status` / `visibility` 出现白名单以外的值时，下拉**原样显示该值**并补一个
  选项，绝不回退成"已发布 / 登录学生可见"——那会让人凭一个并不成立的状态做判断。
- **文件夹行**：列表里文件夹是独立的一行（图标 + "文件夹"字样 + 条目数 + 子级数 + 说明），整行排在
  资料行之前，可进入、可返回上一级。承载 `colSpan` 的那一格必须保持 `table-cell`：在它上面写
  `display:flex` 会让它不再是 table-cell，浏览器替它生成匿名 table-cell 包住，跨列随之失效
  （底色只覆盖到第 2 列、行内动作掉出动作列），弹性布局因此下沉到内层 `.files-folder-main`。
- **两段式删除**：行内「删除」先武装、再确认，确认态把后果写在按钮旁边，并给一条"取消"退路。
- **陈旧快照提示**：见 §5。

Since 2026-10-13/14 the interface matches the backend's abilities (this paragraph used to say that the
title, category, visibility and status were deliberately left without an interface, which no longer
described the implementation):

- **In-row edit entry point**: the edit action sits beside download, save and delete in the row's action
  group. It renders only when `canFileResource(item)` (the backend's `FILE_UPDATE`) is true, and the
  click fetches the detail by id before opening the dialog.
- **Six-field editor**: name, section (category), tags, description, visibility and status. The options
  for section, visibility and status come from `/api/files/config` rather than hand-copied allow-lists.
  Empty tags or an empty description mean *clear*; every field is sent on every save.
- **The save response is written back**: the dialog shows what was actually stored (the title trimmed,
  tags lower-cased, de-duplicated, capped at eight and truncated to 24 characters each), not the raw
  input. A new snapshot from the parent overwrites the whole form; a re-render carrying the same content
  never disturbs what the user is typing.
- **An unknown value is never dressed up**: when `status` or `visibility` carries a value outside the
  allow-list, the select shows that value as it is (with an extra option), never falling back to
  "Published" or "Members", which would have the user judge the resource by a state it does not have.
- **Folder rows**: a folder is its own row in the list (icon, the word "folder", an item count, a child
  count and its description), sitting above every resource row, and can be entered and stepped out of.
  The cell carrying `colSpan` has to stay a `table-cell`: `display:flex` on it stops it being one, the
  browser wraps it in an anonymous table-cell, and the span is lost - the fill then stops inside the
  second column and the in-row action leaves the action column. The flex layout lives on an inner
  `.files-folder-main` instead.
- **Two-step delete**: the in-row delete arms first and confirms second, the confirming state spells out
  the consequence next to the button, and there is a way out.
- **Stale-snapshot notice**: see section 5.
