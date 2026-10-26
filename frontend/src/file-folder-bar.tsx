import { LakoButton, LakoInputBox } from "@lako/ui/components";
import "@lako/ui/ui.css";
import { useState } from "react";
import { useI18n } from "./lib/i18n";
import { folderBreadcrumb, folderById, parentFolderView, viewFolderId, type FolderNode, type FolderView } from "./lib/folder-tree";

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
  scopeHint,
  scopeJumpLabel,
  onScopeJump,
}: {
  folders: FolderNode[];
  view: FolderView;
  onSelect: (view: FolderView) => void;
  canManage: boolean;
  busy: boolean;
  onCreate: (name: string, parentId: number | null) => Promise<void>;
  onRename: (id: number, name: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  // 适用范围提示：在"程序上没有文件夹操作"的视图（如"我的收藏/我的上传"）里，条本身要有内容，
  // 否则会被 .files-folders:empty 的 display:none 整块隐藏，"文件夹在哪"的视觉线索等于不存在。
  // Scope hint: in a view where folder actions make no sense (say "my saved" or "my uploads") the
  // bar still needs content, or .files-folders:empty hides the whole block and the visual cue for
  // "where folders are" stops existing.
  scopeHint?: string;
  scopeJumpLabel?: string;
  onScopeJump?: () => void;
}) {
  const { t } = useI18n();
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const currentId = viewFolderId(view);
  const current = currentId === null ? null : folderById(folders, currentId);
  const trail = folderBreadcrumb(folders, currentId ?? -1);
  // 上一级的目标："全部资料"是平铺的跨文件夹视图，退回根目录；其余层级交给同一个纯函数。
  // The step-up target: the flat cross-folder view returns to the root, and every other level is
  // resolved by the same pure function the list's up-row uses.
  const upTarget: FolderView | null = view === "all" ? "root" : parentFolderView(folders, view);

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
    await run(async () => {
      // 退回哪一级由调用方（页面）决定：它同时掌管列表的取数视图，只有它知道"被删的正是当前视图"。
      // 这里再调 onSelect 会造成两处各写一次视图状态，互相覆盖。
      // Which level to fall back to is the caller's (the page's) decision: it owns the view the list
      // fetches for and is the only place that knows the deleted folder is the current one. Calling
      // onSelect here too would write the view state twice and let the two overwrite each other.
      await onDelete(target.id);
      setNotice(t("file.folderDeleted", { count: target.resourceCount }));
    });
    setConfirmingDelete(false);
  };

  return (
    <section className="files-folders" aria-label={t("file.folders")}>
      {/* 适用范围提示放在最前：个人视图下这一条是条里唯一的内容，也是"文件夹功能存在"的线索。
          The scope hint comes first: under a personal view it is the bar's only content and the cue
          that the folder feature exists at all. */}
      {scopeHint ? (
        <div className="files-folder-scope">
          <span className="files-muted">{scopeHint}</span>
          {onScopeJump ? (
            <LakoButton type="button" variant="ghost" size="sm" onClick={onScopeJump}>
              {scopeJumpLabel ?? t("file.folderAll")}
            </LakoButton>
          ) : null}
        </div>
      ) : null}
      {/* 当前位置：**任何**视图下都渲染，包括根目录与个人标签。
          这一条是本节从"偶尔出现的面包屑"改成"常驻位置条"的关键：管理员与普通用户都要能随时看见
          "我现在在文件系统的哪一层、往上怎么走"，而不是只有进到某个文件夹里才知道。
          它同时解决了一个旧缺陷：非管理员在根目录下本节没有任何内容，会被 .files-folders:empty
          的 display:none 整块隐藏，于是"文件夹功能存在"这件事根本不可见。
          The current location, rendered under **every** view including the root and the personal
          tabs. Turning this from an occasional breadcrumb into a permanent location strip is the key
          change here: administrators and ordinary users alike must be able to see at any moment which
          level of the file system they are on and how to go up, rather than only once they have
          entered a folder. It also removes an old defect: for a non-admin at the root this section
          had no content at all, so .files-folders:empty hid the whole block and the existence of the
          folder feature became invisible. */}
      <nav className="files-folder-crumb files-folder-location" aria-label={t("file.folderBreadcrumb")}>
        {upTarget !== null ? (
          <LakoButton
            type="button"
            variant="secondary"
            size="sm"
            className="files-folder-up"
            onClick={() => onSelect(upTarget)}
          >
            <span aria-hidden="true">{"<"}</span> {t("file.folderUp")}
          </LakoButton>
        ) : null}
        <span className="files-folder-location-label">{t("file.folderPosition")}</span>
        <LakoButton
          type="button"
          variant={current === null && view !== "all" ? "primary" : "ghost"}
          size="sm"
          className="files-folder-root-entry"
          onClick={() => onSelect("root")}
        >
          {t("file.folderRoot")}
        </LakoButton>
        {trail.map((folder) => (
          <span key={folder.id}>
            <span className="files-muted"> / </span>
            <LakoButton type="button" variant="ghost" size="sm" onClick={() => onSelect(folder.id)}>{folder.name}</LakoButton>
          </span>
        ))}
        {/* 跨文件夹的"全部资料"不是路径上的节点，因此单独标在末尾——它必须一眼看出是"额外选项"，
            而不是某个文件夹。 */}
        {/* The cross-folder "everything" view is not a node on the path, so it is marked separately at
            the end: it has to read as an extra option rather than as a folder. */}
        {view === "all" ? <span className="files-folder-all-badge">{t("file.folderAll")}</span> : null}
      </nav>

      {canManage ? (
        <div className="files-folder-actions">
          {creating ? (
            <>
              <LakoInputBox
                containerClassName="files-folder-field"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                maxLength={60}
                placeholder={t("file.folderNamePlaceholder")}
                aria-label={t("file.folderNamePlaceholder")}
              />
              <LakoButton type="button" variant="primary" size="sm" disabled={busy} onClick={() => void submitCreate()}>
                {t("file.folderCreate")}
              </LakoButton>
              <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setCreating(false); setDraft(""); setError(""); }}>
                {t("common.cancel")}
              </LakoButton>
            </>
          ) : (
            <LakoButton type="button" variant="secondary" size="sm" onClick={() => { setCreating(true); setRenaming(false); setConfirmingDelete(false); setError(""); setNotice(""); }}>
              {current ? t("file.folderNewChild") : t("file.folderNew")}
            </LakoButton>
          )}

          {current && renaming ? (
            <>
              <LakoInputBox
                containerClassName="files-folder-field"
                value={renameDraft}
                onChange={(event) => setRenameDraft(event.target.value)}
                maxLength={60}
                aria-label={t("file.folderRename")}
              />
              <LakoButton type="button" variant="primary" size="sm" disabled={busy} onClick={() => void submitRename()}>
                {t("file.folderRenameSubmit")}
              </LakoButton>
              <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setRenaming(false); setError(""); }}>
                {t("common.cancel")}
              </LakoButton>
            </>
          ) : null}

          {current && !renaming ? (
            <LakoButton type="button" variant="secondary" size="sm" onClick={() => { setRenaming(true); setRenameDraft(current.name); setCreating(false); setError(""); setNotice(""); }}>
              {t("file.folderRename")}
            </LakoButton>
          ) : null}

          {current && !renaming && !confirmingDelete ? (
            <LakoButton type="button" variant="secondary" size="sm" className="files-folder-danger" onClick={() => { setConfirmingDelete(true); setError(""); setNotice(""); }}>
              {t("file.folderDelete")}
            </LakoButton>
          ) : null}

          {current && confirmingDelete ? (
            <>
              {/* 删除前必须说清后果：资料会被移到未分类、一份都不会删。 */}
              {/* The consequence is spelled out before deleting: resources move to Unfiled and none
                  of them is deleted. */}
              <span className="files-folder-warn">{t("file.folderDeleteWarning", { count: current.resourceCount })}</span>
              <LakoButton type="button" variant="danger" size="sm" disabled={busy} onClick={() => void submitDelete()}>
                {t("file.folderDeleteConfirm")}
              </LakoButton>
              <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmingDelete(false)}>
                {t("common.cancel")}
              </LakoButton>
            </>
          ) : null}
        </div>
      ) : null}

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="files-folder-notice" role="status">{notice}</p> : null}
    </section>
  );
}
