import { useEffect, useRef, useState, type FormEvent } from "react";
import { Dialog } from "./ui-commons";
import { api, ApiError, type FileConfig, type FileResourceDetail, type FileStatus, type FileVisibility } from "./lib/api";
import { useI18n, type I18nKey } from "./lib/i18n";

// 可见性与状态的文案键：值与后端白名单（VISIBILITIES / STATUSES）逐一对应。
// config 下发的是**值**，界面按值取对应文案；未知值退回默认键，绝不渲染成空白。
// Copy keys for visibility and status: one per value on the backend's allow-lists (VISIBILITIES and
// STATUSES). The config ships the *value*, the interface maps it to copy, and an unknown value falls
// back to a default key rather than rendering blank.
const VISIBILITY_KEYS: Record<string, I18nKey> = {
  public: "file.visibilityPublic",
  members: "file.visibilityMembers",
  private: "file.visibilityPrivate",
};

const STATUS_KEYS: Record<string, I18nKey> = {
  published: "file.statusPublished",
  archived: "file.statusArchived",
};

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

export function FileMetadataDialog({
  detail,
  onClose,
  onSaved,
}: {
  detail: FileResourceDetail;
  onClose: () => void;
  onSaved: (saved: FileResourceDetail) => void;
}) {
  const { t } = useI18n();
  const [title, setTitle] = useState(detail.title);
  const [tags, setTags] = useState(detail.tags.join(", "));
  const [description, setDescription] = useState(detail.descriptionMarkdown);
  const [categoryId, setCategoryId] = useState(String(detail.category.id));
  const [visibility, setVisibility] = useState<FileVisibility>(detail.visibility);
  const [status, setStatus] = useState<FileStatus>(detail.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // 分类/可见性/状态的选项来自 config。config 拉取失败时必须说清楚：这三个下拉会停在"加载中"，
  // 不说明就会被当成页面坏了；同时文件名/标签/简介仍然可编辑、可保存（它们不依赖 config）。
  // The options for category, visibility and status come from the config. A failed config fetch has
  // to be stated: those three selects would otherwise sit on "loading" and read as a broken page.
  // The title, tags and description stay editable and savable in the meantime, since they need no
  // config at all.
  const [config, setConfig] = useState<FileConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

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

  const submit = async (event: FormEvent) => {
    event.preventDefault();
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
      onSaved(saved);
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

  const optionsReady = config !== null;

  return (
    <Dialog
      open
      onOpenChange={(next: boolean) => {
        if (!next) onClose();
      }}
      title={t("file.metaTitle")}
      contentClassName="feedback-modal"
      contentProps={{ "aria-label": t("file.metaTitle") }}
    >
      <form onSubmit={submit}>
        <p className="files-muted files-meta-hint">{t("file.metaHint")}</p>

        <label className="form-field">
          <span>{t("file.metaNameLabel")}</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={160}
            placeholder={t("file.titlePlaceholder")}
            required
          />
        </label>
        <p className="files-muted files-meta-fieldhint">{t("file.metaNameHint")}</p>

        <label className="form-field">
          <span>{t("file.metaCategoryLabel")}</span>
          <select
            value={categoryId}
            disabled={!optionsReady || busy}
            onChange={(event) => setCategoryId(event.target.value)}
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
          </select>
        </label>

        <label className="form-field">
          <span>{t("file.tagsLabel")}</span>
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder={t("file.tagsHint", { count: config?.maxTags ?? 8 })}
            maxLength={300}
          />
        </label>

        <label className="form-field">
          <span>{t("file.description")}</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={6}
            maxLength={20000}
            placeholder={t("file.descriptionPlaceholder")}
          />
        </label>

        <label className="form-field">
          <span>{t("file.metaVisibilityLabel")}</span>
          <select
            value={visibility}
            disabled={!optionsReady || busy}
            onChange={(event) => setVisibility(event.target.value as FileVisibility)}
          >
            {(config?.visibilities ?? []).map((entry) => (
              <option key={entry} value={entry}>{t(VISIBILITY_KEYS[entry] ?? "file.visibilityMembers")}</option>
            ))}
            {!optionsReady ? <option value={visibility}>{t(VISIBILITY_KEYS[visibility] ?? "file.visibilityMembers")}</option> : null}
          </select>
        </label>

        <label className="form-field">
          <span>{t("file.metaStatusLabel")}</span>
          <select
            value={status}
            disabled={!optionsReady || busy}
            onChange={(event) => setStatus(event.target.value as FileStatus)}
          >
            {(config?.statuses ?? []).map((entry) => (
              <option key={entry} value={entry}>{t(STATUS_KEYS[entry] ?? "file.statusPublished")}</option>
            ))}
            {!optionsReady ? <option value={status}>{t(STATUS_KEYS[status] ?? "file.statusPublished")}</option> : null}
          </select>
        </label>
        {/* 下架不是"隐藏一个字段"，而是从列表里消失——后果必须写在控件旁边。 */}
        {/* Archiving is not "hiding a field" but vanishing from the list, so the consequence is
            spelled out next to the control. */}
        {status === "archived" ? (
          <p className="files-muted files-meta-fieldhint">{t("file.statusArchivedHint")}</p>
        ) : null}

        {configError ? <p className="files-muted files-meta-fieldhint" role="status">{configError}</p> : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {notice ? <p className="files-folder-notice" role="status">{notice}</p> : null}

        <div className="modal-actions">
          <button type="button" className="files-link" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="files-primary" disabled={busy}>
            {busy ? t("file.metaSaving") : t("file.metaSave")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
