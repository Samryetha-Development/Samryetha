import { useEffect, useState } from "react";
import type { Submission } from "../api";
import { listSubmissions, approveSubmission, rejectSubmission } from "../api";

function fmtDate(ms: number) {
  return new Date(ms).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
  });
}

export default function AdminPage() {
  const [subs, setSubs] = useState<Submission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | "pending" | "approved" | "rejected">("pending");
  const [actionError, setActionError] = useState<string | null>(null);
  const [rejectId, setRejectId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [working, setWorking] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    listSubmissions({ status: statusFilter || undefined, limit: 200 })
      .then(setSubs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [statusFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const doApprove = async (id: number) => {
    setActionError(null);
    setWorking(id);
    try {
      await approveSubmission(id);
      setSubs((prev) => prev.map((s) => s.id === id ? { ...s, status: "approved" } : s));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Error");
    } finally {
      setWorking(null);
    }
  };

  const doReject = async () => {
    if (rejectId == null) return;
    setActionError(null);
    setWorking(rejectId);
    try {
      await rejectSubmission(rejectId, rejectReason || undefined);
      setSubs((prev) => prev.map((s) => s.id === rejectId ? { ...s, status: "rejected" } : s));
      setRejectId(null);
      setRejectReason("");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Error");
    } finally {
      setWorking(null);
    }
  };

  return (
    <div>
      <h1 className="page-title">Admin Review</h1>
      <p className="page-sub">Review and approve or reject community translation submissions.</p>

      <div className="row" style={{ marginBottom: 20 }}>
        <label className="text-muted text-sm">Filter:</label>
        {(["pending", "approved", "rejected", ""] as const).map((s) => (
          <button
            key={s}
            className={`btn btn-sm ${statusFilter === s ? "btn-accent" : "btn-secondary"}`}
            onClick={() => setStatusFilter(s)}
          >
            {s === "" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      {error && <div className="error-banner" style={{ marginBottom: 16 }}>{error}</div>}
      {actionError && <div className="error-banner" style={{ marginBottom: 16 }}>{actionError}</div>}

      {/* Reject modal */}
      {rejectId != null && (
        <div
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)",
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
          }}
          onClick={(e) => { if (e.target === e.currentTarget) setRejectId(null); }}
        >
          <div className="card col" style={{ maxWidth: 400, width: "90%" }}>
            <div style={{ fontWeight: 640, fontSize: 16 }}>Reject submission #{rejectId}</div>
            <div className="field">
              <label>Reason <span className="text-muted">(optional)</span></label>
              <input
                type="text"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Briefly explain why…"
                autoFocus
              />
            </div>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn-secondary btn-sm" onClick={() => setRejectId(null)}>
                Cancel
              </button>
              <button
                className="btn btn-danger btn-sm"
                onClick={doReject}
                disabled={working === rejectId}
              >
                {working === rejectId ? "Rejecting…" : "Reject"}
              </button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : subs.length === 0 ? (
        <div className="empty-state">No submissions match this filter.</div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Lang</th>
                <th>Translation</th>
                <th>Submitter</th>
                <th>Status</th>
                <th>Date</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {subs.map((s) => (
                <tr key={s.id}>
                  <td><span className="mono">{s.key}</span></td>
                  <td>{s.locale}</td>
                  <td style={{ maxWidth: 220 }}>{s.value}</td>
                  <td className="text-sm">{s.submitter_name}</td>
                  <td>
                    <span className={`badge badge-${s.status}`}>{s.status}</span>
                  </td>
                  <td className="text-muted text-sm">{fmtDate(s.created_at)}</td>
                  <td>
                    {s.status === "pending" ? (
                      <div className="row" style={{ gap: 6 }}>
                        <button
                          className="btn btn-sm btn-accent"
                          disabled={working === s.id}
                          onClick={() => doApprove(s.id)}
                        >
                          Approve
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          disabled={working === s.id}
                          onClick={() => { setRejectId(s.id); setRejectReason(""); }}
                        >
                          Reject
                        </button>
                      </div>
                    ) : (
                      <span className="text-muted text-sm">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
