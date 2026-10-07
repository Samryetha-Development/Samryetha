import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Dialog } from "./ui-commons";
import { api, ApiError, uploadFileBytes, type FileConfig, type FileVisibility } from "./lib/api";
import { formatBytes } from "./lib/format";
import { useI18n, type I18nKey } from "./lib/i18n";

// 资料上传弹窗：选文件 -> 立即校验 -> 填写结构化元数据 -> presign 上传 -> 发布。
// Resource upload dialog: pick a file, validate immediately, fill in structured metadata,
// presign the upload, then publish.
//
// 为什么要"先校验再上传"：体积与格式的失败应当在用户按下发布之前就暴露，
// 而不是让他等一次 50MB 的上传跑完才收到错误。
// Why validate before uploading: a size or format failure must surface before the user
// hits publish, not after waiting for a 50 MB upload to finish.

const VISIBILITY_KEYS: Record<FileVisibility, I18nKey> = {
  public: "file.visibilityPublic",
  members: "file.visibilityMembers",
  private: "file.visibilityPrivate",
};

export function FileUploadDialog({
  config,
  onClose,
  onPublished,
}: {
  config: FileConfig;
  onClose: () => void;
  onPublished: () => void;
}) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [categoryId, setCategoryId] = useState<number>(config.categories[0]?.id ?? 0);
  const [visibility, setVisibility] = useState<FileVisibility>("members");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const extensionOf = (name: string) => {
    const match = /(\.[A-Za-z0-9]+)$/.exec(name);
    return match ? match[1].toLowerCase() : "";
  };

  const onPickFile = (event: ChangeEvent<HTMLInputElement>) => {
    const picked = event.target.files?.[0] ?? null;
    setError("");
    if (!picked) {
      setFile(null);
      return;
    }
    const extension = extensionOf(picked.name);
    if (!config.allowedExtensions.includes(extension)) {
      setError(t("file.badExtension"));
      setFile(null);
      return;
    }
    if (picked.size > config.maxUploadBytes) {
      setError(t("file.fileTooLarge", { size: formatBytes(config.maxUploadBytes) }));
      setFile(null);
      return;
    }
    // 选文件时顺手把文件名填进标题作为初值：多数人上传的资料标题就是文件名去掉扩展名。
    // Seed the title from the filename, since for most uploads the title is just the
    // filename without its extension.
    if (!title) setTitle(picked.name.replace(/\.[A-Za-z0-9]+$/, "").slice(0, 160));
    setFile(picked);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (!file) {
      setError(t("file.fileRequired"));
      return;
    }
    if (!title.trim()) {
      setError(t("file.titleRequired"));
      return;
    }
    setBusy(true);
    try {
      const presign = await api.files.presign({
        filename: file.name,
        mimeType: file.type || "application/octet-stream",
        sizeBytes: file.size,
      });
      await uploadFileBytes(presign.uploadUrl, file);
      await api.files.create({
        objectKey: presign.objectKey,
        expires: presign.expires,
        sig: presign.sig,
        sizeBytes: file.size,
        categoryId,
        title: title.trim(),
        descriptionMarkdown: description,
        tags: tags.split(/[,，、;；]+/).map((entry) => entry.trim()).filter(Boolean),
        visibility,
        originalFilename: file.name,
        mimeType: file.type || "application/octet-stream",
      });
      onPublished();
    } catch (caught) {
      // 保留服务端的具体原因（如"扩展名不允许"），它比通用文案更有助于用户自救。
      // Keep the server's specific reason (an unsupported extension, say); it helps the
      // user recover far better than a generic message.
      if (caught instanceof ApiError && caught.message) setError(caught.message);
      else setError(t("file.uploadFail"));
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
      title={t("file.uploadTitle")}
      contentClassName="feedback-modal"
      contentProps={{ "aria-label": t("file.uploadTitle") }}
    >
      <form onSubmit={submit}>
        <label className="form-field">
          <span>{t("file.chooseFile")}</span>
          <input ref={fileInputRef} type="file" onChange={onPickFile} accept={config.allowedExtensions.join(",")} />
        </label>
        <p className="files-muted files-upload-hint">
          {t("file.extensionHint", { list: config.allowedExtensions.join(" ") })}
        </p>
        {file ? (
          <p className="files-selected">
            {file.name} <span className="files-muted">({formatBytes(file.size)})</span>
          </p>
        ) : null}

        <label className="form-field">
          <span>{t("file.titleLabel")}</span>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={160}
            placeholder={t("file.titlePlaceholder")}
          />
        </label>

        <div className="feedback-field-row">
          <label className="form-field">
            <span>{t("file.categoryLabel")}</span>
            <select value={categoryId} onChange={(event) => setCategoryId(Number(event.target.value))}>
              {config.categories.map((entry) => (
                <option key={entry.id} value={entry.id}>{entry.name}</option>
              ))}
            </select>
          </label>
          <label className="form-field">
            <span>{t("file.visibility")}</span>
            <select value={visibility} onChange={(event) => setVisibility(event.target.value as FileVisibility)}>
              {config.visibilities.map((entry) => (
                <option key={entry} value={entry}>{t(VISIBILITY_KEYS[entry])}</option>
              ))}
            </select>
          </label>
        </div>

        <label className="form-field">
          <span>{t("file.tagsLabel")}</span>
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder={t("file.tagsHint", { count: config.maxTags })}
          />
        </label>

        <label className="form-field">
          <span>{t("file.description")}</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={4}
            maxLength={20000}
            placeholder={t("file.descriptionPlaceholder")}
          />
        </label>

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="modal-actions">
          <button type="button" className="files-link" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button type="submit" className="files-primary" disabled={busy}>
            {busy ? t("file.uploading") : t("file.uploadSubmit")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
