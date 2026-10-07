// 附件转入文件服务弹窗（管理员专属）：选分类 + 填标题 -> 调 /api/files/resources/from-attachment。
// Promote-an-attachment dialog (admins only): pick a category, fill in a title, then call
// /api/files/resources/from-attachment.
//
// 为什么标题要给初值：与上传弹窗一致——多数资料的标题就是附件文件名去掉扩展名，
// 让管理员少敲一遍；后端在标题缺省时同样按这个规则推导，两边行为一致。
// Why seed the title: same reasoning as the upload dialog. For most resources the title is
// the attachment filename without its extension, so the admin does not retype it; the backend
// derives it the same way when the title is omitted, so both sides agree.
//
// 可见性刻意不放进表单：转换入口的可见性缺省与"创建资料"一致，并被源附件受众上限向下夹紧
// （详见 docs/file-service/01-attachment-to-resource.md §6）。需要放宽时走资料详情页的
// 编辑动作，那是一个显式且可审计的独立步骤。
// Visibility is deliberately not part of this form: the promote entry defaults to the same
// value as resource creation and is clamped downwards by the source attachment's audience
// ceiling (see docs/file-service/01-attachment-to-resource.md section 6). Widening is a
// separate, auditable step on the resource detail page.
import { useEffect, useState, type FormEvent } from "react";
import { Dialog } from "./ui-commons";
import { api, ApiError, type FileConfig, type FileResourceDetail } from "./lib/api";
import { useI18n } from "./lib/i18n";

/** 由附件文件名推导标题：去掉扩展名，与后端 _title_from_filename 同规则。 */
export function titleFromFilename(filename: string): string {
  const stem = filename.replace(/\.[A-Za-z0-9]+$/, "").trim();
  return (stem || filename.trim()).slice(0, 160);
}

export function AttachmentPromoteDialog({
  attachmentId,
  attachmentName,
  onClose,
  onPromoted,
}: {
  attachmentId: number;
  attachmentName: string;
  onClose: () => void;
  onPromoted: (resource: FileResourceDetail) => void;
}) {
  const { t } = useI18n();
  const [config, setConfig] = useState<FileConfig | null>(null);
  const [categoryId, setCategoryId] = useState<number>(0);
  const [title, setTitle] = useState(() => titleFromFilename(attachmentName));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // 分类列表来自后端配置接口，不在前端手抄一份，避免与后端漂移。
  // The category list comes from the backend config endpoint instead of being hand-copied
  // into the client, so it cannot drift.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.files.config();
        if (cancelled) return;
        setConfig(data);
        setCategoryId((current) => current || data.categories[0]?.id || 0);
      } catch {
        if (!cancelled) setError(t("file.promoteConfigFail"));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (!title.trim()) {
      setError(t("file.promoteTitleRequired"));
      return;
    }
    if (!categoryId) {
      setError(t("file.promoteConfigFail"));
      return;
    }
    setBusy(true);
    try {
      const resource = await api.files.promoteFromAttachment({
        attachmentId,
        categoryId,
        title: title.trim(),
      });
      onPromoted(resource);
    } catch (caught) {
      // 按状态码给出可自救的文案；403/404 都是后端"不让做"的确定语义。
      // Map the status code to an actionable message; 403 and 404 are both definite
      // "not allowed" answers from the backend.
      if (caught instanceof ApiError) {
        if (caught.status === 401) setError(t("file.promoteSignIn"));
        else if (caught.status === 403) setError(t("file.promoteForbidden"));
        else if (caught.status === 404) setError(t("file.promoteMissing"));
        else if (caught.status === 409) setError(t("file.promoteExists"));
        else setError(t("file.promoteFail"));
      } else {
        setError(t("file.promoteFail"));
      }
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
      title={t("file.promoteTitle")}
      contentClassName="feedback-modal"
      contentProps={{ "aria-label": t("file.promoteTitle") }}
    >
      <form onSubmit={submit}>
        <label className="form-field">
          <span>{t("file.categoryLabel")}</span>
          <select value={categoryId} onChange={(event) => setCategoryId(Number(event.target.value))}>
            {(config?.categories ?? []).map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.name}</option>
            ))}
          </select>
        </label>

        <label className="form-field">
          <span>{t("file.titleLabel")}</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={160}
            placeholder={t("file.titlePlaceholder")}
          />
        </label>

        <p className="files-muted files-upload-hint">{t("file.promoteHint")}</p>

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="modal-actions">
          <button type="button" className="files-link" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="files-primary" disabled={busy}>
            {busy ? t("file.promoting") : t("file.promoteSubmit")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
