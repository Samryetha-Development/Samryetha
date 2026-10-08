import { useRef, useState, type FormEvent } from "react";
import { Dialog } from "./ui-commons";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useAuthModal } from "./auth-modal";
import { useI18n } from "./lib/i18n";

export function ReportButton({ targetType, targetId, className = "ra-btn", onReported }: {
  targetType: "discussion" | "reply";
  targetId: number;
  className?: string;
  onReported: () => void;
}) {
  const { user } = useAuth();
  const { openModal } = useAuthModal();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason.trim() || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      await api.moderation.createReport({ reportableType: targetType, reportableId: targetId, reason: reason.trim() });
      setOpen(false);
      setReason("");
      onReported();
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 401 ? t("auth.sessionExpired") : t("report.failed"));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  return (
    <>
      <button className={className} type="button" onClick={() => {
        if (!user) { openModal("login"); return; }
        setError("");
        setOpen(true);
      }}>{t("report.button")}</button>
      <Dialog open={open} onOpenChange={(next) => { if (!busy) setOpen(next); }} dismissible={!busy}
        title={t(targetType === "discussion" ? "report.discussionTitle" : "report.replyTitle")}
        description={t("report.hideHint")} error={error}>
        <form onSubmit={submit}>
          <label className="form-field">
            <span>{t("report.reason")}</span>
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000}
              rows={4} required disabled={busy} placeholder={t("report.reasonPlaceholder")} />
          </label>
          <div className="modal-actions">
            <button className="admin-btn" type="button" disabled={busy} onClick={() => setOpen(false)}>{t("common.cancel")}</button>
            <button className="admin-btn danger" type="submit" disabled={busy || !reason.trim()}>{t(busy ? "report.submitting" : "report.submit")}</button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
