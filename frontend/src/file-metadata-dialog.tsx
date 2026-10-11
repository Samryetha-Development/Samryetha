import { useState, type FormEvent } from "react";
import { Dialog } from "./ui-commons";
import { api, ApiError, type FileResourceDetail } from "./lib/api";
import { useI18n } from "./lib/i18n";

// 资料元数据编辑弹窗：标签与简介。
// Resource metadata dialog: tags and description.
//
// 权限不在前端发明：入口只在 `detail.can.update` 为真时出现，而那个字段就是后端 authz 的
// `FILE_UPDATE`（管理员 或 该资料的 owner）。后端仍会再判一次，前端隐藏只是界面整洁。
// No permission is invented here: the entry point appears only when `detail.can.update` is true, and
// that field *is* the backend's authz FILE_UPDATE (an admin or the resource's owner). The backend
// checks again; hiding is only cosmetic.
//
// 空值语义按后端定义转达：**空标签列表与空简介表示清空**（后端里"不传该字段"才是不改，
// 而本弹窗每次都会把两个字段都发出去，所以用户按清空就是清空）。失败绝不静默。
// Empty semantics follow the backend: an empty tag list and an empty description mean *clear* (on
// the backend, omitting a field is what means "leave it alone", while this dialog always sends both
// fields). A failure is never silent.

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
  const [tags, setTags] = useState(detail.tags.join(", "));
  const [description, setDescription] = useState(detail.descriptionMarkdown);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const saved = await api.files.update(detail.id, {
        // 与服务端同一套分隔符：半角逗号、全角逗号、顿号、分号都算分隔。
        // The same separators the server accepts: commas, full-width commas, enumeration commas and
        // semicolons.
        tags: tags.split(/[,，、;；]+/).map((entry) => entry.trim()).filter(Boolean),
        descriptionMarkdown: description,
      });
      onSaved(saved);
      setNotice(t("file.metaSaved"));
    } catch (caught) {
      // 保留服务端的具体原因（如"简介超长"），它比通用文案更有助于自助排查。
      // Keep the server's specific reason (an over-long description, say); it helps far more than
      // generic copy.
      if (caught instanceof ApiError && caught.message) setError(caught.message);
      else setError(t("file.metaSaveFail"));
    } finally {
      setBusy(false);
    }
  };

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
          <span>{t("file.tagsLabel")}</span>
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder={t("file.tagsHint", { count: 8 })}
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
