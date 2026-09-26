import { useEffect, useState } from "react";
import type { Submission } from "../api";
import { listSubmissions } from "../api";
import { useNotify } from "../notifications";

function fmtDate(ms: number) {
  return new Date(ms).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
  });
}

export default function MySubmissionsPage() {
  const [subs, setSubs] = useState<Submission[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<"" | "pending" | "approved" | "rejected">("");
  const notify = useNotify();

  useEffect(() => {
    setLoading(true);
    listSubmissions({ status: statusFilter || undefined, limit: 100 })
      .then(setSubs)
      .catch((e) => notify(e instanceof Error ? e.message : "Failed to load submissions.", "error"))
      .finally(() => setLoading(false));
  }, [statusFilter]);

  return (
    <div>
      <h1 className="page-title">My Submissions</h1>
      <p className="page-sub">All translation submissions you have made.</p>

      <div className="row" style={{ marginBottom: 20 }}>
        <label className="text-muted text-sm">Filter:</label>
        {(["", "pending", "approved", "rejected"] as const).map((s) => (
          <button
            key={s}
            className={`btn btn-sm ${statusFilter === s ? "btn-accent" : "btn-secondary"}`}
            onClick={() => setStatusFilter(s)}
          >
            {s === "" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : subs.length === 0 ? (
        <div className="empty-state">No submissions yet.</div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Language</th>
                <th>Translation</th>
                <th>Status</th>
                <th>Submitted</th>
                <th>Note / Reason</th>
              </tr>
            </thead>
            <tbody>
              {subs.map((s) => (
                <tr key={s.id}>
                  <td><span className="mono">{s.key}</span></td>
                  <td>{s.locale}</td>
                  <td style={{ maxWidth: 240 }}>{s.value}</td>
                  <td>
                    <span className={`badge badge-${s.status}`}>{s.status}</span>
                  </td>
                  <td className="text-muted text-sm">{fmtDate(s.created_at)}</td>
                  <td className="text-muted text-sm">
                    {s.status === "rejected" && s.review_note
                      ? <span style={{ color: "var(--danger)" }}>{s.review_note}</span>
                      : s.note ?? "—"}
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
