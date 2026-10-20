import { useDialogExit } from "./lib/use-dialog-exit";
import { LakoButton, LakoInputBox, LakoTextarea, LakoDialog } from "@lako/ui/components";
import "@lako/ui/ui.css";
import { FileSelect } from "./file-select";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, type FileConfig, type FileResourceDetail, type FileStatus, type FileVisibility } from "./lib/api";
import { useI18n, type I18nKey } from "./lib/i18n";

// 可见性与状态的文案键：值与后端白名单（VISIBILITIES / STATUSES）逐一对应。
// config 下发的是**值**，界面按值取对应文案。
// Copy keys for visibility and status: one per value on the backend's allow-lists (VISIBILITIES and
// STATUSES). The config ships the *value* and the interface maps it to copy.
const VISIBILITY_KEYS: Record<string, I18nKey> = {
  public: "file.visibilityPublic",
  members: "file.visibilityMembers",
  private: "file.visibilityPrivate",
};

const STATUS_KEYS: Record<string, I18nKey> = {
  published: "file.statusPublished",
  archived: "file.statusArchived",
};

// 未知值绝不伪装成某个合法状态：映射表里没有的值**原样显示**。此前回退到"已发布 / 登录学生可见"，
// 一旦后端将来新增取值（如 draft），下拉会把它显示成"已发布"，用户据此做出的是错误判断——
// 宁可在界面上露出原始值，也不要让人读到一个不成立的状态。
// An unknown value is never dressed up as a legal one: a value missing from the map is displayed
// as-is. The previous fallback to "Published" / "Members" meant that the moment the backend grew a new
// value (draft, say) the select would show "Published", and the user would judge the resource by a
// state it does not have. Showing the raw value is the honest choice.
function copyFor(keys: Record<string, I18nKey>, value: string, t: (key: I18nKey) => string): string {
  const key = keys[value];
  return key ? t(key) : value;
}

// 一次保存会覆盖的字段快照。用途有二：判断"我打开编辑器之后服务端有没有被别人改过"，
// 以及比较服务端响应是否带来了一份新内容（决定要不要回填表单）。
// A snapshot of every field one save overwrites. It serves two purposes: telling whether the server
// moved underneath the editor while it was open, and telling whether a server response carries a
// genuinely new state (which decides whether the form is written back).
type Snapshot = {
  title: string;
  tags: string;
  description: string;
  categoryId: number;
  visibility: string;
  status: string;
};

function snapshotOf(detail: FileResourceDetail): Snapshot {
  return {
    title: detail.title,
    tags: detail.tags.join(", "),
    description: detail.descriptionMarkdown,
    categoryId: detail.category.id,
    visibility: detail.visibility,
    status: detail.status,
  };
}

function sameSnapshot(left: Snapshot, right: Snapshot): boolean {
  return left.title === right.title
    && left.tags === right.tags
    && left.description === right.description
    && left.categoryId === right.categoryId
    && left.visibility === right.visibility
    && left.status === right.status;
}

const SNAPSHOT_FIELDS: [keyof Snapshot, I18nKey][] = [
  ["title", "file.metaNameLabel"],
  ["categoryId", "file.metaCategoryLabel"],
  ["tags", "file.tagsLabel"],
  ["description", "file.description"],
  ["visibility", "file.metaVisibilityLabel"],
  ["status", "file.metaStatusLabel"],
];

// 哪些字段在两次快照之间不同。返回的是**字段名文案**（不是值），因为界面要做的是
// "告诉用户对方动了什么"，而不是把两份内容都摊开。
// Which fields differ between two snapshots. The result carries the field *labels* rather than the
// values, because the interface has to say what the other editor touched, not dump both versions.
function driftedFields(before: Snapshot, after: Snapshot, t: (key: I18nKey) => string): string[] {
  return SNAPSHOT_FIELDS
    .filter(([field]) => before[field] !== after[field])
    .map(([, key]) => t(key));
}

// 资料编辑弹窗：文件名（标题）、分区（分类）、标签、简介、可见性、状态。
// Resource editor: title, section (category), tags, description, visibility and status.
//
// 权限不在前端发明：入口只在 `detail.can.update` 为真时出现，而那个字段就是后端 authz 的
// `FILE_UPDATE`（管理员 或 该资料的 owner）。后端仍会再判一次，前端隐藏只是界面整洁。
// No permission is invented here: the entry point appears only when `detail.can.update` is true, and
// that field *is* the backend's authz FILE_UPDATE (an admin or the resource's owner). The backend
// checks again; hiding is only cosmetic.
//
// 字段与本后端契约的对应关系（PATCH /api/files/resources/{id}，见 backend 的 ResourcePatchBody）：
// - 文件名 -> `title`（必填、非空，服务端 _flat() 后为空会 422"Title is required"）；
// - 分区   -> `categoryId`（服务端会校验分类存在，不存在即报错）；
// - 标签   -> `tags`；
// - 简介   -> `descriptionMarkdown`（上限 20000，超长由服务端 422，绝不静默截断）；
// - 可见性 -> `visibility`；状态 -> `status`。
// Field-by-field mapping onto the backend contract (PATCH /api/files/resources/{id}, i.e.
// ResourcePatchBody): the title maps to `title` (required and non-empty; the server 422s with
// "Title is required" when _flat() leaves it empty), the section to `categoryId` (the server
// verifies the category exists), tags to `tags`, the description to `descriptionMarkdown` (capped at
// 20000, over-long input is refused with a 422 rather than silently truncated), visibility to
// `visibility` and status to `status`.
//
// 空值语义按后端定义转达：**空标签列表与空简介表示清空**（后端里"不传该字段"才是不改，
// 而本弹窗每次都会把字段全发出去，所以用户按清空就是清空）。失败绝不静默。
// Empty semantics follow the backend: an empty tag list and an empty description mean *clear* (on the
// backend, omitting a field is what means "leave it alone", while this dialog always sends every
// field). A failure is never silent.
//
// 选项来源：分类与可见性来自 `/api/files/config`，**不从后端白名单里手抄**（仓库既有约定：
// router.py 的 files_config docstring 明确"前端不用手抄扩展名白名单/分类列表，避免与后端漂移"）。
// 因此 `status` 也一并由 config 下发（本次新增 `statuses` 字段），而不是在 .tsx 里写死两个字面量。
// Where the options come from: categories and visibilities are read from /api/files/config rather
// than hand-copied out of the backend's allow-lists, which is the repository's stated rule (the
// files_config docstring in router.py says the client must not hand-copy the extension allow-list or
// the category list and drift from the server). `status` follows the same route through a `statuses`
// field added to that config, rather than two literals hard-coded in a .tsx file.
//
// 陈旧快照：保存前先取一次服务端现值比对。本弹窗每次都会把**全量字段**发出去，因此一个陈旧的
// 弹窗不止覆盖标签与简介，还会把可见性与发布状态一起盖回去（别人刚下架的资料会被静默重新发布）。
// 处理方式是"提示 + 让用户确认"，**不引入乐观锁**——仓库口径是后写覆盖（见
// backend/docs/file-service-metadata.md §5），服务端不做版本校验、也不拒绝写入，收敛仍在服务端那一层。
// Stale snapshots: the server's current values are fetched and compared before saving. This dialog
// sends every field on every save, so a stale one overwrites not just tags and description but
// visibility and publication status too - a resource somebody else just archived would be silently
// republished. The answer is a notice plus an explicit confirmation, *not* optimistic locking: the
// repository's stated rule is last-write-wins (backend/docs/file-service-metadata.md section 5), the
// server performs no version check and rejects nothing, and last-write-wins remains the contract.
export function FileMetadataDialog({
  detail,
  onClose: onClosed,
  onSaved,
}: {
  detail: FileResourceDetail;
  onClose: () => void;
  onSaved: (saved: FileResourceDetail) => void;
}) {
  const { t } = useI18n();
  const { visible, close: onClose } = useDialogExit(onClosed);
  const [title, setTitle] = useState(detail.title);
  const [tags, setTags] = useState(detail.tags.join(", "));
  const [description, setDescription] = useState(detail.descriptionMarkdown);
  const [categoryId, setCategoryId] = useState(String(detail.category.id));
  const [visibility, setVisibility] = useState<FileVisibility>(detail.visibility);
  const [status, setStatus] = useState<FileStatus>(detail.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // 检测到他人改动时列出的字段名；非空即"这次保存先停下，等用户确认"。
  // The field labels found to have moved elsewhere; a non-empty list means this save stops and waits
  // for the user to confirm.
  const [conflict, setConflict] = useState<string[]>([]);
  // 分类/可见性/状态的选项来自 config。config 拉取失败时必须说清楚：这三个下拉会停在"加载中"，
  // 不说明就会被当成页面坏了；同时文件名/标签/简介仍然可编辑、可保存（它们不依赖 config）。
  // The options for category, visibility and status come from the config. A failed config fetch has
  // to be stated: those three selects would otherwise sit on "loading" and read as a broken page.
  // The title, tags and description stay editable and savable in the meantime, since they need no
  // config at all.
  const [config, setConfig] = useState<FileConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const mountedRef = useRef(true);
  // 最近一次灌进表单的服务端快照。用来区分"父组件换了一份新内容"与"父组件用同一个对象重渲染"，
  // 前者要整体回填、后者绝不能动用户正在编辑的输入。
  // The server snapshot most recently written into the form. It separates "the parent handed over new
  // content", which must be written back wholesale, from "the parent re-rendered with the same
  // object", which must never disturb what the user is typing.
  const appliedRef = useRef<Snapshot>(snapshotOf(detail));

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // 把一份服务端快照整体灌进表单并记为"已知的服务端状态"。既用于保存成功后的归一化结果回填，
  // 也用于冲突后的重新载入。
  // Writes a whole server snapshot into the form and records it as the known server state. Used both
  // for writing a normalised save response back and for reloading after a conflict.
  const applyDetail = useCallback((next: FileResourceDetail) => {
    appliedRef.current = snapshotOf(next);
    setTitle(next.title);
    setTags(next.tags.join(", "));
    setDescription(next.descriptionMarkdown);
    setCategoryId(String(next.category.id));
    setVisibility(next.visibility);
    setStatus(next.status);
  }, []);

  // 服务端归一化结果必须回填到用户眼前：容器（父组件）把响应体交回来之后，表单里的值要变成
  // **真正落库的那一份**，而不是用户敲进去的原文。`useState(detail.x)` 只在挂载时取一次初值，
  // 没有这一步，输入"  线性代数  "、贴 12 个标签、写大写标签的人，看到的会是自己输入的原样，
  // 而库里存的是去空白、小写去重、只留前 8 个、单个截断 24 字符之后的结果——界面与数据不一致。
  // The normalised result has to land in front of the user: once the parent hands the response body
  // back, the form must show what was actually stored rather than what was typed. `useState(detail.x)`
  // only reads its initial value at mount, so without this step somebody who typed "  线性代数  ",
  // pasted twelve tags or used upper-case ones keeps seeing their own input while the database holds
  // the trimmed, lower-cased, de-duplicated, first-eight-only, 24-char-truncated version - the screen
  // and the data disagree.
  //
  // 只在内容真的变了时才回填：同一份内容的重渲染不能覆盖用户正在编辑的输入。
  // Written back only when the content actually changed: a re-render carrying the same content must
  // not clobber what the user is typing.
  useEffect(() => {
    const next = snapshotOf(detail);
    if (sameSnapshot(next, appliedRef.current)) return;
    applyDetail(detail);
  }, [detail, applyDetail]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.files.config();
        if (mountedRef.current && !cancelled) setConfig(data);
      } catch (caught) {
        if (mountedRef.current && !cancelled) {
          if (caught instanceof ApiError && caught.message) setConfigError(caught.message);
          else setConfigError(t("file.metaOptionsFail"));
        }
      }
    })();
    return () => { cancelled = true; };
  }, [t]);

  // 保存。`force` 为真表示用户已经在冲突提示上确认过"仍然保存"，这一次跳过比对直接覆盖。
  // Saving. `force` means the user already confirmed "save anyway" on the conflict notice, so the
  // comparison is skipped and this save overwrites.
  const save = async (force: boolean) => {
    setError("");
    setNotice("");
    // 文件名是必填项：空白名会让服务端 422，与其白跑一趟请求，不如就地拦下并说清楚。
    // The title is required: a blank one would come back as a 422, and catching it here with a plain
    // explanation beats a wasted round trip.
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      setError(t("file.titleRequired"));
      return;
    }
    setBusy(true);
    try {
      if (!force) {
        // 陈旧快照保护见文件头部的说明：只提示、只等确认，不做乐观锁、不拒绝写入。
        // The stale-snapshot guard is described at the top of this file: it notices and waits for a
        // confirmation, with no optimistic locking and no refusal to write.
        // 取不到服务端现值时**不保存**：宁可让用户重试一次，也不要在"不知道对方改了什么"的情况下
        // 把可见性与发布状态盖回去。失败必须说出来，不许静默降级成"照常保存"。
        // When the server's current values cannot be read, nothing is saved: better one retry than
        // overwriting visibility and publication status without knowing what the other editor did. The
        // failure is stated, never quietly degraded into "save as usual".
        let latest: FileResourceDetail;
        try {
          latest = await api.files.get(detail.id);
        } catch (caught) {
          if (caught instanceof ApiError && caught.message) setError(caught.message);
          else setError(t("file.metaCheckFail"));
          setConflict([]);
          return;
        }
        const drifted = driftedFields(appliedRef.current, snapshotOf(latest), t);
        if (drifted.length > 0) {
          setConflict(drifted);
          return;
        }
      }
      const saved = await api.files.update(detail.id, {
        title: trimmedTitle,
        // 与服务端同一套分隔符：半角逗号、全角逗号、顿号、分号都算分隔。
        // The same separators the server accepts: commas, full-width commas, enumeration commas and
        // semicolons.
        tags: tags.split(/[,，、;；]+/).map((entry) => entry.trim()).filter(Boolean),
        descriptionMarkdown: description,
        // 分区（分类）只在 config 到位时发出：拿不到分类列表就发不出一个可信的 id，
        // 而"不发"在后端恰好等于"不改"，语义正确。
        // The section (category) is only sent once the config has landed: without the category list
        // there is no trustworthy id to send, and omitting the field means "leave it alone" on the
        // backend, which is exactly the right semantics.
        ...(config ? { categoryId: Number(categoryId) } : {}),
        visibility,
        status,
      });
      // 先回填服务端归一化结果，再通知父组件：两处都写，弹窗就不依赖父组件是否顺手把响应体
      // 传回来（少一处依赖，就少一种"界面与库不一致"的可能）。
      // The normalised response is written back first and the parent told afterwards: doing both means
      // the dialog does not depend on the parent remembering to hand the response back, and one fewer
      // dependency is one fewer way for the screen and the database to disagree.
      applyDetail(saved);
      onSaved(saved);
      setConflict([]);
      setNotice(t("file.metaSaved"));
    } catch (caught) {
      // 保留服务端的具体原因（如"简介超长"、"分类不存在"），它比通用文案更有助于自助排查。
      // Keep the server's specific reason (an over-long description, a missing category); it helps
      // far more than generic copy.
      if (caught instanceof ApiError && caught.message) setError(caught.message);
      else setError(t("file.metaSaveFail"));
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void save(false);
  };

  // 冲突提示里的"放弃我的改动并重新载入"：把服务端现值灌回表单，用户的输入被丢弃。
  // "Discard my changes and reload" on the conflict notice: the server's current values are written
  // into the form and the user's input is dropped.
  const reloadFromServer = async () => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const latest = await api.files.get(detail.id);
      applyDetail(latest);
      setConflict([]);
    } catch (caught) {
      if (caught instanceof ApiError && caught.message) setError(caught.message);
      else setError(t("file.metaOpenFail"));
    } finally {
      setBusy(false);
    }
  };

  const optionsReady = config !== null;
  const visibilityOptions = config?.visibilities ?? [];
  const statusOptions = config?.statuses ?? [];

  return (
    <LakoDialog
      open={visible}
      onOpenChange={(next: boolean) => {
        if (!next) onClose();
      }}
      title={t("file.metaTitle")}
      className="files-ui-dialog" ariaLabel={t("file.metaTitle")}
    >
      <form onSubmit={submit}>
        <p className="files-muted files-meta-hint">{t("file.metaHint")}</p>

        <LakoInputBox label={t("file.metaNameLabel")}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={160}
            placeholder={t("file.titlePlaceholder")}
            required />
        <p className="files-muted files-meta-fieldhint">{t("file.metaNameHint")}</p>

        <div className="form-field"><span>{t("file.metaCategoryLabel")}</span><FileSelect ariaLabel={t("file.metaCategoryLabel")}
            value={categoryId}
            disabled={!optionsReady || busy}
            onValueChange={(value) => setCategoryId(value)}
          >
            {(config?.categories ?? []).map((entry) => (
              <option key={entry.id} value={String(entry.id)}>{entry.name}</option>
            ))}
            {/* config 未就绪时仍渲染当前分类，免得下拉显示成空、让人以为这份资料没有分类。 */}
            {/* While the config is still in flight the current category is rendered anyway, so the
                select never shows empty and suggests the resource has no category. */}
            {optionsReady && (config?.categories ?? []).every((entry) => entry.id !== Number(categoryId))
              ? <option value={categoryId}>{detail.category.name}</option>
              : null}
            {!optionsReady ? <option value={categoryId}>{detail.category.name}</option> : null}
          </FileSelect></div>

        <LakoInputBox label={t("file.tagsLabel")}
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder={t("file.tagsHint", { count: config?.maxTags ?? 8 })}
            maxLength={300} />

        <LakoTextarea label={t("file.description")}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={6}
            maxLength={20000}
            placeholder={t("file.descriptionPlaceholder")} />

        <div className="form-field"><span>{t("file.metaVisibilityLabel")}</span><FileSelect ariaLabel={t("file.metaVisibilityLabel")}
            value={visibility}
            disabled={!optionsReady || busy}
            onValueChange={(value) => setVisibility(value as FileVisibility)}
          >
            {visibilityOptions.map((entry) => (
              <option key={entry} value={entry}>{copyFor(VISIBILITY_KEYS, entry, t)}</option>
            ))}
            {/* 当前值不在 config 的清单里（后端新增了取值、或 config 未就绪）：补一个选项把**原值**
                显示出来。少了它下拉会显示成空白，多了它至少不会把未知值说成"登录学生可见"。 */}
            {/* The current value is missing from the config list (the backend grew a value, or the
                config has not landed): an extra option shows the *raw* value. Without it the select
                renders blank; with it, an unknown value is at least never called "Members". */}
            {!visibilityOptions.includes(visibility)
              ? <option value={visibility}>{copyFor(VISIBILITY_KEYS, visibility, t)}</option>
              : null}
          </FileSelect></div>

        <div className="form-field"><span>{t("file.metaStatusLabel")}</span><FileSelect ariaLabel={t("file.metaStatusLabel")}
            value={status}
            disabled={!optionsReady || busy}
            onValueChange={(value) => setStatus(value as FileStatus)}
          >
            {statusOptions.map((entry) => (
              <option key={entry} value={entry}>{copyFor(STATUS_KEYS, entry, t)}</option>
            ))}
            {/* 同可见性：未知状态原样显示，不伪装成"已发布"。 */}
            {/* Same as visibility: an unknown status is shown as-is rather than passed off as
                "Published". */}
            {!statusOptions.includes(status)
              ? <option value={status}>{copyFor(STATUS_KEYS, status, t)}</option>
              : null}
          </FileSelect></div>
        {/* 下架不是"隐藏一个字段"，而是从列表里消失——后果必须写在控件旁边。 */}
        {/* Archiving is not "hiding a field" but vanishing from the list, so the consequence is
            spelled out next to the control. */}
        {status === "archived" ? (
          <p className="files-muted files-meta-fieldhint">{t("file.statusArchivedHint")}</p>
        ) : null}

        {/* 陈旧快照提示：说明对方动了哪些字段，并让用户在"仍然保存"与"重新载入"之间做选择。
            两个按钮都是 type="button"：绝不能让"仍然保存"顺带触发一次未确认的表单提交。 */}
        {/* Stale-snapshot notice: it names the fields that moved elsewhere and lets the user choose
            between saving anyway and reloading. Both buttons are type="button" so that "save anyway"
            can never double as an unconfirmed form submit. */}
        {conflict.length > 0 ? (
          <div className="files-meta-conflict" role="alert">
            <p>{t("file.metaConflict")}</p>
            <p className="files-muted">{t("file.metaConflictFields", { fields: conflict.join(", ") })}</p>
            <div className="modal-actions">
              <LakoButton type="button" variant="ghost" size="sm" onClick={() => void reloadFromServer()} disabled={busy}>
                {t("file.metaConflictReload")}
              </LakoButton>
              <LakoButton type="button" variant="primary" size="sm" onClick={() => void save(true)} disabled={busy}>
                {t("file.metaConflictSave")}
              </LakoButton>
            </div>
          </div>
        ) : null}

        {configError ? <p className="files-muted files-meta-fieldhint" role="status">{configError}</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {notice ? <p className="files-folder-notice" role="status">{notice}</p> : null}

        <div className="modal-actions">
          <LakoButton type="button" variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </LakoButton>
          <LakoButton type="submit" variant="primary" size="sm" disabled={busy}>
            {busy ? t("file.metaSaving") : t("file.metaSave")}
          </LakoButton>
        </div>
      </form>
    </LakoDialog>
  );
}
