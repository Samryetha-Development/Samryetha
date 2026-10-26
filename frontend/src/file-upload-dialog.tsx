import { useDialogExit } from "./lib/use-dialog-exit";
import { LakoButton, LakoInputBox, LakoTextarea, LakoDialog } from "@lako/ui/components";
import "@lako/ui/ui.css";
import { FileSelect } from "./file-select";
import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { api, ApiError, uploadFileBytes, type FileConfig, type FileVisibility } from "./lib/api";
import { formatBytes } from "./lib/format";
import { flattenFolders, folderPathLabel } from "./lib/folder-tree";
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
  defaultFolderId,
  onClose: onClosed,
  onPublished,
}: {
  config: FileConfig;
  // 当前所在文件夹：在文件夹里点上传就是归入该文件夹。null 表示根目录（不属于任何文件夹）。
  // The folder currently open: uploading from inside one files the resource there. null means the
  // root, i.e. no folder.
  defaultFolderId: number | null;
  onClose: () => void;
  // 回调带上实际落点的文件夹 id，让页面能把视图切到真正的目标位置，而不是让用户在
  // "我的上传"里猜它到底进了哪个文件夹。
  // The callback carries the folder the resource actually landed in, so the page can move the view to
  // the real destination instead of leaving the user to guess from "my uploads".
  onPublished: (folderId: number | null) => void;
}) {
  const { t } = useI18n();
  const { visible, close: onClose } = useDialogExit(onClosed);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [categoryId, setCategoryId] = useState<number>(config.categories[0]?.id ?? 0);
  const [visibility, setVisibility] = useState<FileVisibility>("members");
  // 目标文件夹：初值就是当前所在位置，用空串表示根目录（与 <select> 的 value 口径一致）。
  // The destination folder starts at the current location; an empty string means the root, matching
  // the <select> value contract.
  const [folderId, setFolderId] = useState<string>(defaultFolderId === null ? "" : String(defaultFolderId));
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
        // 归属在发布时一次写定：先传到根目录再移动会留下一个"短暂出现在根视图"的中间态。
        // The placement is written once at publish time: uploading to the root and moving afterwards
        // would leave a transient state where the resource briefly sits in the root view.
        folderId: folderId === "" ? null : Number(folderId),
      });
      onPublished(folderId === "" ? null : Number(folderId));
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
    <LakoDialog
      open={visible}
      onOpenChange={(next: boolean) => {
        if (!next && !busy) onClose();
      }}
      title={t("file.uploadTitle")}
      dismissible={!busy}
      className="files-ui-dialog files-upload-dialog" ariaLabel={t("file.uploadTitle")}
    >
      <form onSubmit={submit} aria-busy={busy}>
        <LakoButton className="files-upload-close" variant="ghost" aria-label={t("common.close")} disabled={busy} onClick={onClose}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
        </LakoButton>
        <div className="files-upload-picker">
          <input hidden ref={fileInputRef} type="file" disabled={busy} onChange={onPickFile} accept={config.allowedExtensions.join(",")} aria-label={t("file.chooseFile")} />
          <svg className="files-upload-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 3h7l4 4v14H6V3h1Zm7 0v5h4M9 13h6m-6 4h6" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /></svg>
          <div className="files-upload-selection">
            <strong>{file ? file.name : t("file.uploadSelectHint")}</strong>
            <span>{file ? formatBytes(file.size) : t("file.uploadSizeHint", { size: formatBytes(config.maxUploadBytes) })}</span>
          </div>
          <LakoButton variant="secondary" size="sm" disabled={busy} onClick={() => fileInputRef.current?.click()}>{t(file ? "file.uploadReplace" : "file.chooseFile")}</LakoButton>
        </div>

        <LakoInputBox label={t("file.titleLabel")}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            disabled={busy}
            required
            maxLength={160}
            placeholder={t("file.titlePlaceholder")} />

        <div className="feedback-field-row">
          <div className="form-field"><span>{t("file.categoryLabel")}</span><FileSelect disabled={busy} ariaLabel={t("file.categoryLabel")} value={categoryId} onValueChange={(value) => setCategoryId(Number(value))}>
              {config.categories.map((entry) => (
                <option key={entry.id} value={entry.id}>{entry.name}</option>
              ))}
            </FileSelect></div>
          <div className="form-field"><span>{t("file.visibility")}</span><FileSelect disabled={busy} ariaLabel={t("file.visibility")} value={visibility} onValueChange={(value) => setVisibility(value as FileVisibility)}>
              {config.visibilities.map((entry) => (
                <option key={entry} value={entry}>{t(VISIBILITY_KEYS[entry])}</option>
              ))}
            </FileSelect></div>
        </div>

        {/* 目标文件夹：必须显眼。旧弹窗根本没有这一项，上传的资料只能先落在根目录、再手工移动，
            而"移动到文件夹"的入口又恰恰是管理员抱怨看不出来的那一个按钮——两件事叠起来就成了
            "移入文件夹的功能像是没有"。
            The destination folder has to be prominent. The old dialog had no such field at all, so
            every upload landed at the root and had to be moved by hand, while the "move to folder"
            entry point was precisely the button the administrator complained was unrecognisable.
            Together those two made the whole feature look absent. */}
        <div className="form-field files-upload-folder">
          <span>{t("file.folderTarget")}</span>
          <FileSelect ariaLabel={t("file.folderTarget")} disabled={busy} value={folderId} onValueChange={(value) => setFolderId(value)}>
            <option value="">{t("file.folderRoot")}</option>
            {flattenFolders(config.folders ?? []).map(({ folder, depth }) => (
              <option key={folder.id} value={String(folder.id)}>
                {"\u00a0\u00a0".repeat(depth)}{folderPathLabel(config.folders ?? [], folder.id)}
              </option>
            ))}
          </FileSelect>
          <span className="files-muted">{t("file.folderTargetHint")}</span>
        </div>

        <LakoInputBox label={t("file.tagsLabel")}
            disabled={busy}
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder={t("file.tagsHint", { count: config.maxTags })} />

        <LakoTextarea label={t("file.description")}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            disabled={busy}
            rows={3}
            maxLength={20000}
            placeholder={t("file.descriptionPlaceholder")} />

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="files-upload-actions">
          <LakoButton type="button" variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </LakoButton>
          <LakoButton type="submit" variant="primary" size="md" loading={busy} disabled={busy || !file || !title.trim()}>
            {busy ? t("file.uploading") : t("file.uploadSubmit")}
          </LakoButton>
        </div>
      </form>
    </LakoDialog>
  );
}
