import { useEffect, useMemo, useState } from "react";
import { LakoDialog, LakoInputBox } from "@lako/ui";
import type { Submission, SubmissionNote } from "../api";
import { addSubmissionNote, approveSubmission, listSubmissionNotes, listSubmissions, rejectSubmission } from "../api";
import { useNotify } from "../notifications";

function fmtDate(ms: number) {
  return new Date(ms).toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export default function AdminPage() {
  const [subs, setSubs] = useState<Submission[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<"" | "pending" | "approved" | "rejected">("pending");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [details, setDetails] = useState<Submission | null>(null);
  const [notes, setNotes] = useState<SubmissionNote[]>([]);
  const [notesLoading, setNotesLoading] = useState(false);
  const [newNote, setNewNote] = useState("");
  const [addingNote, setAddingNote] = useState(false);
  const [reviewAction, setReviewAction] = useState<"approve" | "reject" | null>(null);
  const [reviewTargets, setReviewTargets] = useState<number[]>([]);
  const [reviewNote, setReviewNote] = useState("");
  const [working, setWorking] = useState(false);
  const notify = useNotify();

  const load = () => {
    setLoading(true);
    listSubmissions({ status: statusFilter || undefined, limit: 200 })
      .then((items) => {
        setSubs(items);
        setSelected(new Set());
      })
      .catch((error) => notify(error instanceof Error ? error.message : "Failed to load submissions.", "error"))
      .finally(() => setLoading(false));
  };

  useEffect(load, [statusFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const pendingIds = useMemo(() => subs.filter((submission) => submission.status === "pending").map((submission) => submission.id), [subs]);
  const selectedPending = pendingIds.filter((id) => selected.has(id));
  const allPendingSelected = pendingIds.length > 0 && selectedPending.length === pendingIds.length;

  const beginReview = (action: "approve" | "reject", ids: number[]) => {
    setReviewAction(action);
    setReviewTargets(ids);
    setReviewNote("");
  };

  const openDetails = async (submission: Submission) => {
    setDetails(submission);
    setNotes([]);
    setNewNote("");
    setNotesLoading(true);
    try {
      setNotes(await listSubmissionNotes(submission.id));
    } catch (error) {
      notify(error instanceof Error ? error.message : "Failed to load notes.", "error");
    } finally {
      setNotesLoading(false);
    }
  };

  const postNote = async () => {
    if (!details || !newNote.trim()) return;
    setAddingNote(true);
    try {
      const note = await addSubmissionNote(details.id, newNote.trim());
      setNotes((current) => [...current, note]);
      setNewNote("");
      notify("Note added.", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Failed to add note.", "error");
    } finally {
      setAddingNote(false);
    }
  };

  const finishReview = async () => {
    if (!reviewAction || reviewTargets.length === 0) return;
    setWorking(true);
    try {
      const results = await Promise.all(reviewTargets.map((id) => reviewAction === "approve"
        ? approveSubmission(id, reviewNote.trim() || undefined)
        : rejectSubmission(id, reviewNote.trim() || undefined)));
      const byId = new Map(results.map((submission) => [submission.id, submission]));
      setSubs((current) => current.map((submission) => byId.get(submission.id) ?? submission));
      if (details && byId.has(details.id)) setDetails(byId.get(details.id) ?? null);
      setSelected((current) => {
        const next = new Set(current);
        reviewTargets.forEach((id) => next.delete(id));
        return next;
      });
      notify(`${reviewTargets.length} submission${reviewTargets.length === 1 ? "" : "s"} ${reviewAction === "approve" ? "approved" : "rejected"}.`, "success");
      setReviewAction(null);
      setReviewTargets([]);
      setReviewNote("");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Review action failed.", "error");
    } finally {
      setWorking(false);
    }
  };

  return (
    <div>
      <h1 className="page-title">Admin Review</h1>
      <p className="page-sub">Review community translations, inspect notes, and process related submissions together.</p>

      <div className="admin-toolbar">
        <div className="row">
          <span className="text-muted">Filter</span>
          {(["pending", "approved", "rejected", ""] as const).map((status) => (
            <button key={status} className={`btn btn-sm ${statusFilter === status ? "btn-accent" : "btn-secondary"}`} onClick={() => setStatusFilter(status)}>
              {status === "" ? "All" : status.charAt(0).toUpperCase() + status.slice(1)}
            </button>
          ))}
        </div>
        {selectedPending.length > 0 && (
          <div className="row admin-bulk-actions">
            <span className="text-muted">{selectedPending.length} selected</span>
            <button className="btn btn-sm btn-accent" onClick={() => beginReview("approve", selectedPending)}>Approve</button>
            <button className="btn btn-sm btn-danger" onClick={() => beginReview("reject", selectedPending)}>Reject</button>
          </div>
        )}
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : subs.length === 0 ? (
        <div className="empty-state">No submissions match this filter.</div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead><tr>
              <th className="selection-cell"><input type="checkbox" aria-label="Select all pending submissions" checked={allPendingSelected} disabled={pendingIds.length === 0} onChange={() => setSelected(allPendingSelected ? new Set() : new Set(pendingIds))} /></th>
              <th>Key</th><th>Lang</th><th>Translation</th><th>Submitter</th><th>Status</th><th>Date</th><th>Actions</th>
            </tr></thead>
            <tbody>{subs.map((submission) => (
              <tr key={submission.id}>
                <td className="selection-cell"><input type="checkbox" aria-label={`Select submission ${submission.id}`} checked={selected.has(submission.id)} disabled={submission.status !== "pending"} onChange={() => setSelected((current) => {
                  const next = new Set(current);
                  if (next.has(submission.id)) next.delete(submission.id); else next.add(submission.id);
                  return next;
                })} /></td>
                <td><span className="mono">{submission.key}</span></td>
                <td>{submission.locale}</td>
                <td className="translation-cell">{submission.value}</td>
                <td className="text-sm">{submission.submitter_name}</td>
                <td><span className={`badge badge-${submission.status}`}>{submission.status}</span></td>
                <td className="text-muted text-sm">{fmtDate(submission.created_at)}</td>
                <td><div className="row table-actions">
                  <button className="btn btn-sm btn-secondary" onClick={() => void openDetails(submission)}>Details</button>
                  {submission.status === "pending" && <>
                    <button className="btn btn-sm btn-accent" onClick={() => beginReview("approve", [submission.id])}>Approve</button>
                    <button className="btn btn-sm btn-danger" onClick={() => beginReview("reject", [submission.id])}>Reject</button>
                  </>}
                </div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      <LakoDialog open={details != null} onOpenChange={(open) => { if (!open) { setDetails(null); setNotes([]); setNewNote(""); } }} title={details ? `Submission #${details.id}` : "Submission details"} description={details ? `${details.key} · ${details.locale}` : undefined} className="submission-details-dialog" actions={details?.status === "pending" ? <>
        <button className="btn btn-secondary" onClick={() => setDetails(null)}>Close</button>
        <button className="btn btn-accent" onClick={() => beginReview("approve", [details.id])}>Approve</button>
        <button className="btn btn-danger" onClick={() => beginReview("reject", [details.id])}>Reject</button>
      </> : <button className="btn btn-secondary" onClick={() => setDetails(null)}>Close</button>}>
        {details && <div className="submission-details">
          <section><span className="detail-label">Translation</span><p>{details.value}</p></section>
          <section><span className="detail-label">Activity</span><div className="review-thread">
            <article><strong>{details.submitter_name}</strong><time>{fmtDate(details.created_at)}</time><p>{details.note || "Submitted this translation without an additional note."}</p></article>
            {details.reviewed_at && <article><strong>{details.reviewer_name || "Reviewer"}</strong><time>{fmtDate(details.reviewed_at)}</time><p>{details.review_note || `${details.status === "approved" ? "Approved" : "Rejected"} this submission.`}</p></article>}
            {notes.map((note) => <article key={note.id}><strong>{note.author_name}</strong><time>{fmtDate(note.created_at)}</time><p>{note.body}</p></article>)}
            {notesLoading && <p className="thread-loading">Loading notes…</p>}
          </div></section>
          <section className="note-composer">
            <span className="detail-label">Add note</span>
            <div className="row">
              <LakoInputBox value={newNote} onChange={(event) => setNewNote(event.target.value)} placeholder="Add context or leave a comment…" aria-label="Add note" onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void postNote(); } }} />
              <button className="btn btn-accent" disabled={addingNote || !newNote.trim()} onClick={() => void postNote()}>{addingNote ? "Adding…" : "Add note"}</button>
            </div>
          </section>
        </div>}
      </LakoDialog>

      <LakoDialog open={reviewAction != null} onOpenChange={(open) => { if (!open && !working) setReviewAction(null); }} title={`${reviewAction === "approve" ? "Approve" : "Reject"} ${reviewTargets.length} submission${reviewTargets.length === 1 ? "" : "s"}?`} description={reviewAction === "approve" ? "Approved translations become available in the catalog." : "Rejected submissions stay visible to their authors with your review note."} actions={<>
        <button className="btn btn-secondary" disabled={working} onClick={() => setReviewAction(null)}>Cancel</button>
        <button className={`btn ${reviewAction === "reject" ? "btn-danger" : "btn-accent"}`} disabled={working} onClick={finishReview}>{working ? "Working…" : reviewAction === "approve" ? "Approve" : "Reject"}</button>
      </>}>
        <div className="field"><label>Review note <span className="text-muted">(optional)</span></label><LakoInputBox value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} placeholder="Add context for the submitter…" aria-label="Review note" /></div>
      </LakoDialog>
    </div>
  );
}
