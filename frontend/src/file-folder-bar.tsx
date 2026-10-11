import { useState } from "react";
import { useI18n } from "./lib/i18n";
import { flattenFolders, folderBreadcrumb, folderById, viewFolderId, type FolderNode, type FolderView } from "./lib/folder-tree";

// 文件夹导航条：全部 / 未分类 / 各层级文件夹 + 面包屑 +（管理员）建/改名/删。
// The folder bar: "All", "Unfiled" and every folder at its own level, plus a breadcrumb and, for
// admins, create / rename / delete.
//
// 失败绝不静默：这里每个动作都把异常接住并显示出来（服务端给的原因优先），
// 同时向上抛给调用方的错误回调，让页面顶部横幅也拿得到同一条信息。
// A failure is never silent: every action catches its exception and shows it, preferring the
// server's own reason, while the caller's error callback receives the same message so the page
// banner can show it too.

export function FileFolderBar({
  folders,
  view,
  onSelect,
  canManage,
  busy,
  onCreate,
  onRename,
  onDelete,
}: {
  folders: FolderNode[];
  view: FolderView;
  onSelect: (view: FolderView) => void;
  canManage: boolean;
  busy: boolean;
  onCreate: (name: string, parentId: number | null) => Promise<void>;
  onRename: (id: number, name: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  const { t } = useI18n();
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const rows = flattenFolders(folders);
  const currentId = viewFolderId(view);
  const current = currentId === null ? null : folderById(folders, currentId);
  const trail = folderBreadcrumb(folders, currentId ?? -1);

  const run = async (action: () => Promise<void>) => {
    setError("");
    setNotice("");
    try {
      await action();
      return true;
    } catch (caught) {
      // 服务端的具体原因（如"同层已有同名文件夹"）比通用文案更有助于自助排查。
      // The server's specific reason (a duplicate name at this level, say) helps far more than
      // generic copy.
      setError(caught instanceof Error && caught.message ? caught.message : t("file.folderFail"));
      return false;
    }
  };

  const submitCreate = async () => {
    const name = draft.trim();
    if (!name) {
      setError(t("file.folderNameRequired"));
      return;
    }
    const ok = await run(async () => {
      // 在某个文件夹里点「新建」就建在它下面，否则建在顶层——省掉一次"建完再移动"。
      // Creating while a folder is open nests under it, otherwise it lands at the top level, which
      // saves a create-then-move round trip.
      await onCreate(name, currentId);
      setNotice(t("file.folderCreated", { name }));
    });
    if (ok) {
      setDraft("");
      setCreating(false);
    }
  };

  const submitRename = async () => {
    if (current === null) return;
    const name = renameDraft.trim();
    if (!name) {
      setError(t("file.folderNameRequired"));
      return;
    }
    const ok = await run(async () => {
      await onRename(current.id, name);
      setNotice(t("file.folderRenamed", { name }));
    });
    if (ok) setRenaming(false);
  };

  const submitDelete = async () => {
    if (current === null) return;
    const target = current;
    const ok = await run(async () => {
      await onDelete(target.id);
      setNotice(t("file.folderDeleted", { count: target.resourceCount }));
    });
    setConfirmingDelete(false);
    if (ok) onSelect("all");
  };

  return (
    <section className="files-folders" aria-label={t("file.folders")}>
      <div className="files-folder-chips">
        <button
          type="button"
          className={`files-chip ${view === "all" ? "active" : ""}`}
          aria-current={view === "all" ? "true" : undefined}
          onClick={() => onSelect("all")}
        >
          {t("file.folderAll")}
        </button>
        <button
          type="button"
          className={`files-chip ${view === "unfiled" ? "active" : ""}`}
          aria-current={view === "unfiled" ? "true" : undefined}
          onClick={() => onSelect("unfiled")}
        >
          {t("file.folderUnfiled")}
        </button>
        {rows.map(({ folder, depth }) => (
          <button
            key={folder.id}
            type="button"
            className={`files-chip files-folder-chip ${view === folder.id ? "active" : ""}`}
            aria-current={view === folder.id ? "true" : undefined}
            style={{ marginInlineStart: depth * 12 }}
            onClick={() => onSelect(folder.id)}
          >
            {folder.name}
            <span className="files-chip-count">{folder.resourceCount}</span>
          </button>
        ))}
        {rows.length === 0 ? <span className="files-muted">{t("file.folderEmpty")}</span> : null}
      </div>

      {/* 面包屑：只在真正进到某个文件夹时出现，"全部/未分类"不是路径上的节点。
          The breadcrumb appears only inside a real folder; "All" and "Unfiled" are not nodes on a path. */}
      {current ? (
        <nav className="files-folder-crumb" aria-label={t("file.folderBreadcrumb")}>
          <button type="button" className="files-link" onClick={() => onSelect("all")}>{t("file.folderAll")}</button>
          {trail.map((folder) => (
            <span key={folder.id}>
              <span className="files-muted"> / </span>
              <button type="button" className="files-link" onClick={() => onSelect(folder.id)}>{folder.name}</button>
            </span>
          ))}
        </nav>
      ) : null}

      {canManage ? (
        <div className="files-folder-actions">
          {creating ? (
            <>
              <input
                className="files-folder-input"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                maxLength={60}
                placeholder={t("file.folderNamePlaceholder")}
                aria-label={t("file.folderNamePlaceholder")}
              />
              <button type="button" className="files-primary" disabled={busy} onClick={() => void submitCreate()}>
                {t("file.folderCreate")}
              </button>
              <button type="button" className="files-link" disabled={busy} onClick={() => { setCreating(false); setDraft(""); setError(""); }}>
                {t("common.cancel")}
              </button>
            </>
          ) : (
            <button type="button" className="files-link" onClick={() => { setCreating(true); setRenaming(false); setConfirmingDelete(false); setError(""); setNotice(""); }}>
              {current ? t("file.folderNewChild") : t("file.folderNew")}
            </button>
          )}

          {current && renaming ? (
            <>
              <input
                className="files-folder-input"
                value={renameDraft}
                onChange={(event) => setRenameDraft(event.target.value)}
                maxLength={60}
                aria-label={t("file.folderRename")}
              />
              <button type="button" className="files-primary" disabled={busy} onClick={() => void submitRename()}>
                {t("file.folderRenameSubmit")}
              </button>
              <button type="button" className="files-link" disabled={busy} onClick={() => { setRenaming(false); setError(""); }}>
                {t("common.cancel")}
              </button>
            </>
          ) : null}

          {current && !renaming ? (
            <button type="button" className="files-link" onClick={() => { setRenaming(true); setRenameDraft(current.name); setCreating(false); setError(""); setNotice(""); }}>
              {t("file.folderRename")}
            </button>
          ) : null}

          {current && !renaming && !confirmingDelete ? (
            <button type="button" className="files-link" onClick={() => { setConfirmingDelete(true); setError(""); setNotice(""); }}>
              {t("file.folderDelete")}
            </button>
          ) : null}

          {current && confirmingDelete ? (
            <>
              {/* 删除前必须说清后果：资料会被移到未分类、一份都不会删。 */}
              {/* The consequence is spelled out before deleting: resources move to Unfiled and none
                  of them is deleted. */}
              <span className="files-folder-warn">{t("file.folderDeleteWarning", { count: current.resourceCount })}</span>
              <button type="button" className="files-danger" disabled={busy} onClick={() => void submitDelete()}>
                {t("file.folderDeleteConfirm")}
              </button>
              <button type="button" className="files-link" disabled={busy} onClick={() => setConfirmingDelete(false)}>
                {t("common.cancel")}
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="files-folder-notice" role="status">{notice}</p> : null}
    </section>
  );
}
