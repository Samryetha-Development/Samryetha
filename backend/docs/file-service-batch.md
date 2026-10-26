# 文件服务·批量操作（Stage 3）

File-service batch operations (stage 3)

本文记录批量操作的能力分层、接口形状、逐条结果语义与几个刻意做出的取舍。
This document records the ability tiers, the endpoint shapes, the per-row result semantics and a few
deliberate trade-offs of the batch feature.

## 1. 定位：批量下载是读者能力，批量管理是治理能力

Batch download is a reader ability; batch management is a governance ability.

| 操作 | 能力 | 判定 |
|---|---|---|
| 批量下载（打包 ZIP） | `FILE_BATCH_DOWNLOAD` | `is_active(actor)` — **任何 active 账户** |
| 批量移动 / 复制 / 删除 / 重命名 / 批量编辑 | `FILE_BATCH_MANAGE` | `is_global_mod(actor)` — **仅全局管理员** |

这条分界是需求原文的直接翻译：「只有管理员可以批量管理文件和批量编辑文件；普通用户只能批量下载」。
The boundary is a direct translation of the requirement: only administrators may batch manage and
batch edit files, while ordinary users may only batch download.

为什么要把下载单独拆成一个能力，而不是复用 `FILE_READ` 逐个调用：一次批量下载是**一个**用户可见
动作，逐个调用会让界面不得不自己编排 N 个请求并处理"第 7 个失败"这种中间态。拆成独立能力也让
"将来收紧下载"与"收紧管理"互不牵连。
Why download is its own ability rather than N calls to `FILE_READ`: a batch download is **one**
user-visible action, and driving it as N requests forces the client to orchestrate them and to handle
the intermediate state of "the seventh one failed". A separate ability also keeps "tighten downloads
later" and "tighten management later" independent of each other.

`FILE_BATCH_MANAGE` 刻意与单项的 `FILE_UPDATE` / `FILE_DELETE` 分开：一次误操作的半径是 N 份而不
是 1 份，把两者合成一条能力会让"允许某人改一份资料"顺带允许他改一百份。
`FILE_BATCH_MANAGE` is deliberately separate from the single-resource `FILE_UPDATE` and
`FILE_DELETE`: one misclick here has a blast radius of N resources rather than one, and folding them
into a single ability would let "may edit one resource" imply "may edit a hundred".

## 2. 接口

API

| 方法与路径 | 说明 |
|---|---|
| `POST /api/files/batch/download` | `{ids[]}` → **直接返回 ZIP 字节**（`application/zip`） |
| `POST /api/files/batch/move` | `{ids[], folderId?}` → `BatchResult` |
| `POST /api/files/batch/copy` | `{ids[], folderId?}` → `BatchResult` |
| `POST /api/files/batch/delete` | `{ids[]}` → `BatchResult` |
| `POST /api/files/batch/rename` | `{ids[], mode, prefix?, suffix?, find?, replace?}` → `BatchResult` |
| `POST /api/files/batch/edit` | `{ids[], categoryId?, visibility?, status?, tags?}` → `BatchResult` |

路由层只做认证（`require_active_user`），"能不能做"一律在 service 层经 authz 判定——沿用
`files/router.py` 顶部写明的既有约定。
The routes only authenticate; whether an actor may do something is decided in the service layer
through authz, following the convention stated at the top of `files/router.py`.

## 3. 逐条结果（`BatchResult`）

Per-row results

```json
{
  "requested": 2,
  "succeeded": 1,
  "failed": 1,
  "results": [
    { "id": 12, "ok": true },
    { "id": 99, "ok": false, "error": "Resource not found" }
  ]
}
```

**失败必须逐条可见，且必须独立于其余条目。** 批量动作最坏的形态是"看起来成功、实际有几条没做"：
那会让调用方以为数据已经统一，之后再拿这份错误认知去做别的决定。因此除"规则本身不合法"（例如
`mode=replace` 却没给 `find`）整批 400 之外，单条失败绝不让整个请求失败。
**Failures stay visible per row and independent of the others.** The worst shape of a batch action is
"looked like it worked while a few rows did not", which leaves the caller believing the data is
uniform and making further decisions on that belief. So apart from an invalid rule (say
`mode=replace` with no `find`), which rejects the whole batch with 400, a per-row failure never fails
the whole request.

失败原因优先取业务错误原文（例如 `Resource not found`），其他异常只保留类名，避免把内部细节
泄漏给客户端。
The failure reason prefers the business error's own text (say `Resource not found`); other exceptions
contribute only their class name, so internal detail never leaks to the client.

## 4. 批量下载的几个取舍

Trade-offs in batch download

1. **打包而不是发 N 条签名链接**：一次批量下载要给用户一个能直接落盘的产物，而 N 条链接要在
   浏览器里点 N 次。
   One artifact the user can save directly, instead of N signed links that take N clicks.
2. **任何一条读不到就整批 403**：用户拿到的压缩包要么完整，要么因为明确的理由失败。偷偷少放
   几份，用户会以为那几份资料真的丢了。
   Any single unreadable id rejects the whole batch with 403: the archive is either complete or it
   failed for a stated reason. Silently dropping entries reads as "those files are gone".
3. **每取走一份都记一次下载量**，与单条下载同口径——否则批量通道就成了"下载计数永远不涨"的旁路。
   Every retrieved resource records a download, exactly like the single-resource path; otherwise the
   batch channel becomes a side door where the counter never moves.
4. **压缩包条目名挡路径穿越、同名文件加序号**：上传的文件名可能带 `..` 或目录分隔符（只保留最后
   一段），而一堆 `scan.jpg` 若不加序号，后写的会盖掉先写的，用户拿到的包看起来"少了几份"。
   Entry names block path traversal (only the last segment survives) and duplicate names gain a
   numeric suffix; without it a pile of `scan.jpg` would overwrite each other and the archive would
   look like it lost files.

## 5. 批量复制的取舍

Copy trade-offs

`file_resources.object_key` 上有 **UNIQUE 约束**（一份上传只对应一份资料），因此"多行共享同一个
存储对象"在库层面根本不成立——实测直接撞 `IntegrityError`。所以副本必须持有一个新的 object_key：
The `file_resources.object_key` column carries a **UNIQUE constraint** (one upload backs exactly one
resource), so "several rows sharing one stored object" cannot exist at the database level at all —
measured as a plain `IntegrityError`. A copy therefore has to own a new object key:

1. 用 `Storage.create_upload_session()` 生成新的 object_key；
2. 用 **`os.link` 硬链接**把原字节挂到新 key 上（跨卷/不支持硬链接的文件系统退回
   `shutil.copyfile`）。硬链接在文件系统层面共享同一份数据块，所以"复制 20 份"几乎不额外占磁盘，
   而两条行各自持有独立 key，删掉其中一条绝不会影响另一条；
3. insert 失败时把已写下的字节 `Storage.delete_object()` 删掉——留下一个没有任何资料行认领的
   对象，就是只能等 24 小时孤儿清理才消失的垃圾文件。

1. Mint a new object key through `Storage.create_upload_session()`.
2. Hard-link the original bytes onto the new key with **`os.link`**, falling back to
   `shutil.copyfile` across volumes or on filesystems without hard links. A hard link shares the same
   data blocks, so "copy twenty rows" costs almost no extra disk, while each row owns an independent
   key and removing one can never affect the other.
3. On a failed insert, reclaim the written bytes through `Storage.delete_object()`: an object no
   resource row claims is garbage that only the 24-hour orphan sweep would ever remove.

副本保留原 `uploader`（溯源不因复制而改写），计数类字段（下载/收藏/评分）归零，标题追加
`（副本）` 以便区分。
A copy keeps the original uploader (copying must not rewrite provenance), starts the counter fields
at zero, and appends `（副本）` to the title so the two can be told apart.

## 6. 选择集与幂等

Selection and idempotence

- 上限 **50 条**，与列表页单页上限 `MAX_PAGE_SIZE` 对齐：界面一次最多勾选一页，后端按同一口径
  设限，免得出现"界面能勾、后端拒收"的错位。空集合回 422。
  The cap is **50**, matching the listing page's `MAX_PAGE_SIZE`: the interface can only tick one page
  at a time and the backend draws the line in the same place. An empty selection answers 422.
- **重复 id 只执行一次**。去重是安全要求而非洁癖：批量删除里同一个 id 出现两次，第二次会对着
  已经软删的行再删一次，审计上就多出一条"某人删了它两次"的假记录。
  **A repeated id executes once.** De-duplication is a safety requirement rather than tidiness: in a
  batch delete the same id twice would hit an already soft-deleted row again and leave a false audit
  trail reading "this person deleted it twice".

## 7. 审计与"没变就不写"

Auditing, and "no change, no write"

批量移动/删除/重命名/编辑逐条写一条 `moderation_actions`：
`file.resource.set_folder` / `file.resource.delete` / `file.update_metadata` / `file.resource.copy`，
`reason` 里以 `batch` 开头以便与单条操作区分。
Every batch move, delete, rename and edit writes one `moderation_actions` row
(`file.resource.set_folder` / `file.resource.delete` / `file.update_metadata` /
`file.resource.copy`), with `reason` starting with `batch` so it can be told apart from single-row
operations.

**只有真正变了才写、才留审计。** 批量动作常带着"已经是对的"那些条目，给没变化的行记一笔
"改成了原值"只会把真正的改动淹掉。
**Only a real change is written and audited.** A batch action routinely includes rows that are
already correct, and recording "changed to the value it already had" for those would bury the genuine
changes.

## 8. 界面口径

Interface

- 勾选列放在表格最左；表头全选**只针对当前这一页**——跨页选择会让"我到底选了哪些"无从核对。
- 选中集合在换页 / 换标签 / 换文件夹 / 换搜索条件时清空：让一批"用户已经看不见的行"留在选择集里，
  等于让批量删除作用到屏幕之外。
- 批量工具条对所有标签页可用；**「批量下载」对所有人渲染**，移动/复制/重命名/批量编辑/删除只在
  管理员时渲染。界面隐藏只省掉一次注定失败的点击，**绝不承担安全职责**——后端对同一动作仍有
  权威判定，非管理员直接拿到 403。
- 删除、以及复制/移动/重命名/编辑都在弹窗里完成；删除必须二次确认并写明条数与不可撤回。

- The selection column sits first; the header toggle covers **only the current page**, because a
  cross-page selection makes it impossible to verify what was actually ticked.
- The selection clears on page, tab, folder or search changes: leaving ids the user can no longer see
  in the selection would let a batch delete reach rows off-screen.
- The batch toolbar is available on every tab; **"Download selected" renders for everyone**, while
  move, copy, rename, batch edit and delete render only for administrators. Hiding a control only
  spares a click that was doomed, and **never carries the security duty** — the backend still decides
  authoritatively and refuses a non-admin with 403.
- Delete, and the copy/move/rename/edit forms, live in dialogs; a batch delete needs a second
  confirmation that states the count and that it cannot be undone from the interface.

## 9. 验证

Verification

| 层 | 证据 |
|---|---|
| 后端行为 | `backend/tests/test_files_batch.py`（24 个用例：能力分层双向、逐条结果、审计、软删语义、选择集上限、重复 id、空集合、匿名拒绝） |
| 前端行为 | `frontend/scripts/files-batch-smoke.cjs`（jsdom + 真实源码 + 负对照：管理按钮对普通用户必须不出现；失败必须可见） |
| 真实 HTTP | 起真实 uvicorn，用真实 cookie 分别扮演管理员 / 普通用户 / 匿名，逐条记录状态码——管理员全部 200、普通用户管理类全部 403 FORBIDDEN、普通用户批量下载 200 且拿到可解压 ZIP、匿名全部 401 |
