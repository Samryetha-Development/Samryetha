import { LakoButton, LakoDialog, LakoInputBox } from "@lako/ui/components";
import "@lako/ui/ui.css";
import { useState } from "react";
import { FileSelect } from "./file-select";
import { useI18n } from "./lib/i18n";
import { flattenFolders, folderPathLabel, type FolderNode } from "./lib/folder-tree";

// 批量操作工具条：勾选若干条资料后出现。
// The batch toolbar, which appears once several resources are selected.
//
// 权限分层就体现在这里：**「批量下载」对所有登录用户可以见**（普通用户必须能批量下载），
// 而移动/复制/重命名/批量编辑/删除只在 canManage 为真（管理员）时渲染。这只是界面上的收敛，
// 后端对同一个动作仍然有权威判定，非管理员直接拿到 403——界面隐藏只是省掉一次注定失败的点击，
// 绝不承担安全职责。
// The ability tiers show up right here: **"Download selected" is visible to every signed-in user**
// (ordinary users must be able to batch download), while move, copy, rename, batch edit and delete
// render only when canManage is true (an administrator). This is interface narrowing alone: the
// backend still decides authoritatively for the same action and refuses a non-admin with 403.
// Hiding a control only spares a click that was doomed, and never carries the security duty.
//
// 失败绝不静默：每个动作都把异常接住并显示服务端给出的原因，逐条失败时把失败条数与原因一起说清。
// A failure is never silent: every action catches its exception and shows the server's own reason,
// and a partial failure states both the number of failed rows and why.

export type BatchActionOutcome = {
  // 请求条数与成功条数；失败原因为服务端逐条给出，这里只汇总展示。
  // The requested and succeeded counts; the failure reasons arrive per row from the server and are
  // summarised here.
  requested: number;
  succeeded: number;
  reasons: string[];
};

export function FileBatchBar({
  count,
  canManage,
  folders,
  categories,
  busy,
  onClear,
  onDownload,
  onMove,
  onCopy,
  onRename,
  onEdit,
  onDelete,
}: {
  count: number;
  canManage: boolean;
  folders: FolderNode[];
  categories: Array<{ id: number; name: string }>;
  busy: boolean;
  onClear: () => void;
  onDownload: () => Promise<void>;
  onMove: (folderId: number | null) => Promise<BatchActionOutcome>;
  onCopy: (folderId: number | null) => Promise<BatchActionOutcome>;
  onRename: (input: { mode: "prefix" | "suffix" | "replace"; prefix: string; suffix: string; find: string; replace: string }) => Promise<BatchActionOutcome>;
  onEdit: (input: { categoryId?: number; visibility?: string; status?: string }) => Promise<BatchActionOutcome>;
  onDelete: () => Promise<BatchActionOutcome>;
}) {
  const { t } = useI18n();
  const [dialog, setDialog] = useState<"move" | "copy" | "rename" | "edit" | "delete" | null>(null);
  const [target, setTarget] = useState("");
  const [renameMode, setRenameMode] = useState<"prefix" | "suffix" | "replace">("prefix");
  const [renameText, setRenameText] = useState("");
  const [renameFind, setRenameFind] = useState("");
  const [renameReplaceWith, setRenameReplaceWith] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editVisibility, setEditVisibility] = useState("");
  const [editStatus, setEditStatus] = useState("");
  const [formError, setFormError] = useState("");

  // 空选择时整条工具条不渲染：一个"0 项已选"的条带只会占掉列表的高度。
  // The whole toolbar stays unmounted on an empty selection: a "0 selected" strip would only eat
  // the vertical space the list needs.
  if (count === 0) return null;

  const closeDialog = (keep: boolean) => {
    if (busy) return;
    if (!keep) {
      setDialog(null);
      setFormError("");
    }
  };

  // 汇总文案由页面持有（工具条在动作成功后因选择被清空而卸载，放在这里会随之消失）。
  // The summary copy lives in the page: the toolbar unmounts once the selection clears after a
  // successful action, so a message rendered here would vanish with it.
  const runAction = async (action: () => Promise<BatchActionOutcome>, done?: () => void) => {
    setFormError("");
    try {
      await action();
      done?.();
    } catch (caught) {
      // 服务端的具体原因（例如 403 的 FORBIDDEN）优先显示，它比通用文案有用得多。
      // The server's own reason (the 403 FORBIDDEN body, say) wins over generic copy: it tells the
      // user what actually happened.
      setFormError(caught instanceof Error && caught.message ? caught.message : t("file.batchFail"));
    }
  };

  const submitDelete = async () => {
    await runAction(onDelete, () => setDialog(null));
  };

  const submitMove = async () => {
    await runAction(() => onMove(target === "" ? null : Number(target)), () => setDialog(null));
  };

  const submitCopy = async () => {
    await runAction(() => onCopy(target === "" ? null : Number(target)), () => setDialog(null));
  };

  const submitRename = async () => {
    if (renameMode === "replace" && !renameFind.trim()) {
      setFormError(t("file.batchRenameNeedle"));
      return;
    }
    await runAction(
      () => onRename({ mode: renameMode, prefix: renameMode === "prefix" ? renameText : "", suffix: renameMode === "suffix" ? renameText : "", find: renameFind, replace: renameReplaceWith }),
      () => setDialog(null),
    );
  };

  const submitEdit = async () => {
    await runAction(
      () => onEdit({
        categoryId: editCategory === "" ? undefined : Number(editCategory),
        visibility: editVisibility === "" ? undefined : editVisibility,
        status: editStatus === "" ? undefined : editStatus,
      }),
      () => setDialog(null),
    );
  };

  return (
    <section className="files-batch" aria-label={t("file.batchSelect")}>
      <div className="files-batch-head">
        <span className="files-batch-count">{t("file.batchSelected", { count })}</span>
        {/* 批量下载对所有人渲染：这是普通用户唯一被允许的批量动作。 */}
        {/* Batch download renders for everyone: it is the one batch action ordinary users may use. */}
        <LakoButton type="button" variant="primary" size="sm" disabled={busy} onClick={() => void onDownload()}>
          {t("file.batchDownload")}
        </LakoButton>
        {canManage ? (
          <>
            <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setTarget(""); setDialog("move"); setFormError(""); }}>
              {t("file.batchMove")}
            </LakoButton>
            <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setTarget(""); setDialog("copy"); setFormError(""); }}>
              {t("file.batchCopy")}
            </LakoButton>
            <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setDialog("rename"); setFormError(""); }}>
              {t("file.batchRename")}
            </LakoButton>
            <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setEditCategory(""); setEditVisibility(""); setEditStatus(""); setDialog("edit"); setFormError(""); }}>
              {t("file.batchEdit")}
            </LakoButton>
            <LakoButton type="button" variant="danger" size="sm" disabled={busy} onClick={() => { setDialog("delete"); setFormError(""); }}>
              {t("file.batchDelete")}
            </LakoButton>
          </>
        ) : null}
        <LakoButton type="button" variant="ghost" size="sm" disabled={busy} onClick={onClear}>
          {t("file.batchClear")}
        </LakoButton>
      </div>
      {dialog === "move" || dialog === "copy" ? (
        <LakoDialog
          open
          onOpenChange={(open) => closeDialog(open)}
          dismissible={!busy}
          title={dialog === "move" ? t("file.batchMoveTitle") : t("file.batchCopyTitle")}
          description={t("file.batchSelected", { count })}
          className="files-ui-dialog"
          actions={
            <>
              <LakoButton variant="ghost" disabled={busy} onClick={() => closeDialog(false)}>{t("common.cancel")}</LakoButton>
              <LakoButton variant="primary" disabled={busy} onClick={() => void (dialog === "move" ? submitMove() : submitCopy())}>
                {dialog === "move" ? t("file.batchMove") : t("file.batchCopy")}
              </LakoButton>
            </>
          }
        >
          <div className="form-field">
            <span>{t("file.batchTargetFolder")}</span>
            <FileSelect ariaLabel={t("file.batchTargetFolder")} value={target} disabled={busy} onValueChange={setTarget}>
              {/* 复制可以"保持各自当前归属"，移动则必须落到某一个明确的目标上；
                  移动若允许留空，就会把"没选文件夹"静默解释成"全部移出到未分类"。 */}
              {/* A copy may keep each resource where it already sits, while a move must name one
                  explicit target: letting a move stay blank would silently read "no folder chosen"
                  as "move everything to unfiled". */}
              {dialog === "copy" ? <option value="">{t("file.batchKeepFolder")}</option> : null}
              {dialog === "move" ? <option value="">{t("file.folderUnfiled")}</option> : null}
              {flattenFolders(folders).map(({ folder }) => (
                <option key={folder.id} value={folder.id}>{folderPathLabel(folders, folder.id)}</option>
              ))}
            </FileSelect>
          </div>
          {formError ? <p className="form-error" role="alert">{formError}</p> : null}
        </LakoDialog>
      ) : null}

      {dialog === "rename" ? (
        <LakoDialog
          open
          onOpenChange={(open) => closeDialog(open)}
          dismissible={!busy}
          title={t("file.batchRenameTitle")}
          description={t("file.batchSelected", { count })}
          className="files-ui-dialog"
          actions={
            <>
              <LakoButton variant="ghost" disabled={busy} onClick={() => closeDialog(false)}>{t("common.cancel")}</LakoButton>
              <LakoButton variant="primary" disabled={busy} onClick={() => void submitRename()}>{t("file.batchRename")}</LakoButton>
            </>
          }
        >
          <div className="form-field">
            <span>{t("file.batchRenameMode")}</span>
            <FileSelect ariaLabel={t("file.batchRenameMode")} value={renameMode} disabled={busy}
              onValueChange={(value) => setRenameMode(value === "suffix" ? "suffix" : value === "replace" ? "replace" : "prefix")}>
              <option value="prefix">{t("file.batchRenamePrefix")}</option>
              <option value="suffix">{t("file.batchRenameSuffix")}</option>
              <option value="replace">{t("file.batchRenameReplace")}</option>
            </FileSelect>
          </div>
          {renameMode === "replace" ? (
            <>
              <div className="form-field">
                <span>{t("file.batchRenameFind")}</span>
                <LakoInputBox containerClassName="files-batch-field" value={renameFind} maxLength={160} disabled={busy}
                  onChange={(event) => setRenameFind(event.target.value)} aria-label={t("file.batchRenameFind")} />
              </div>
              <div className="form-field">
                <span>{t("file.batchRenameReplaceWith")}</span>
                <LakoInputBox containerClassName="files-batch-field" value={renameReplaceWith} maxLength={160} disabled={busy}
                  onChange={(event) => setRenameReplaceWith(event.target.value)} aria-label={t("file.batchRenameReplaceWith")} />
              </div>
            </>
          ) : (
            <div className="form-field">
              <span>{renameMode === "prefix" ? t("file.batchRenamePrefix") : t("file.batchRenameSuffix")}</span>
              <LakoInputBox containerClassName="files-batch-field" value={renameText} maxLength={60} disabled={busy}
                onChange={(event) => setRenameText(event.target.value)} aria-label={t("file.batchRenameMode")} />
            </div>
          )}
          {formError ? <p className="form-error" role="alert">{formError}</p> : null}
        </LakoDialog>
      ) : null}

      {dialog === "edit" ? (
        <LakoDialog
          open
          onOpenChange={(open) => closeDialog(open)}
          dismissible={!busy}
          title={t("file.batchEditTitle")}
          description={t("file.batchEditHint")}
          className="files-ui-dialog"
          actions={
            <>
              <LakoButton variant="ghost" disabled={busy} onClick={() => closeDialog(false)}>{t("common.cancel")}</LakoButton>
              <LakoButton variant="primary" disabled={busy} onClick={() => void submitEdit()}>{t("file.batchEdit")}</LakoButton>
            </>
          }
        >
          <div className="form-field">
            <span>{t("file.categoryLabel")}</span>
            <FileSelect ariaLabel={t("file.categoryLabel")} value={editCategory} disabled={busy} onValueChange={setEditCategory}>
              <option value="">{t("file.batchKeepValue")}</option>
              {categories.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}
            </FileSelect>
          </div>
          <div className="form-field">
            <span>{t("file.visibility")}</span>
            <FileSelect ariaLabel={t("file.visibility")} value={editVisibility} disabled={busy} onValueChange={setEditVisibility}>
              <option value="">{t("file.batchKeepValue")}</option>
              <option value="public">{t("file.visibilityPublic")}</option>
              <option value="members">{t("file.visibilityMembers")}</option>
              <option value="private">{t("file.visibilityPrivate")}</option>
            </FileSelect>
          </div>
          <div className="form-field">
            <span>{t("file.statusLabel")}</span>
            <FileSelect ariaLabel={t("file.statusLabel")} value={editStatus} disabled={busy} onValueChange={setEditStatus}>
              <option value="">{t("file.batchKeepValue")}</option>
              <option value="published">{t("file.statusPublished")}</option>
              <option value="archived">{t("file.statusArchived")}</option>
            </FileSelect>
          </div>
          {formError ? <p className="form-error" role="alert">{formError}</p> : null}
        </LakoDialog>
      ) : null}

      {dialog === "delete" ? (
        <LakoDialog
          open
          onOpenChange={(open) => closeDialog(open)}
          dismissible={!busy}
          title={t("file.batchDelete")}
          description={t("file.batchDeleteWarning", { count })}
          className="files-ui-dialog"
          actions={
            <>
              <LakoButton variant="ghost" disabled={busy} onClick={() => closeDialog(false)}>{t("common.cancel")}</LakoButton>
              <LakoButton variant="danger" disabled={busy} onClick={() => void submitDelete()}>{t("file.batchDeleteConfirm")}</LakoButton>
            </>
          }
        >
          {formError ? <p className="form-error" role="alert">{formError}</p> : null}
        </LakoDialog>
      ) : null}
    </section>
  );
}
