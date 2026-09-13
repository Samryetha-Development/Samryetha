import { useEffect, useState, FormEvent } from "react";
import type { CatalogEntry, User } from "../api";
import { getCatalog, submitTranslation } from "../api";

const LANGS = [
  { code: "zh-Hans", label: "Chinese (Simplified)" },
  { code: "zh-Hant", label: "Chinese (Traditional)" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "es", label: "Spanish" },
  { code: "pt", label: "Portuguese" },
];

interface Props {
  user: User;
  onSubmitted: () => void;
}

export default function SubmitPage({ onSubmitted }: Props) {
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [key, setKey] = useState("");
  const [lang, setLang] = useState(LANGS[0].code);
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    getCatalog().then(setEntries).catch(() => null);
  }, []);

  const selectedEntry = entries.find((e) => e.key === key);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!key || !lang || !value.trim()) return;
    setError(null);
    setLoading(true);
    try {
      await submitTranslation({ key, lang, value: value.trim(), note: note.trim() || undefined });
      setSuccess(true);
      setValue("");
      setNote("");
      setTimeout(() => {
        setSuccess(false);
        onSubmitted();
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 560 }}>
      <h1 className="page-title">Submit a Translation</h1>
      <p className="page-sub">
        Pick a source string and provide your translation. Submissions are reviewed before being published.
      </p>

      <form onSubmit={handleSubmit} className="col">
        {error && <div className="error-banner">{error}</div>}
        {success && <div className="success-banner">Submitted! Redirecting…</div>}

        <div className="field">
          <label htmlFor="key">Source string</label>
          <select
            id="key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            required
          >
            <option value="">Select a string…</option>
            {entries.map((e) => (
              <option key={e.key} value={e.key}>
                [{e.key}] {e.value.slice(0, 60)}{e.value.length > 60 ? "…" : ""}
              </option>
            ))}
          </select>
        </div>

        {selectedEntry && (
          <div className="card" style={{ padding: "14px 16px" }}>
            <div className="text-muted text-sm" style={{ marginBottom: 4 }}>Source text</div>
            <div style={{ fontSize: 15 }}>{selectedEntry.value}</div>
            {selectedEntry.context && (
              <div className="text-muted text-sm" style={{ marginTop: 6 }}>
                Context: {selectedEntry.context}
              </div>
            )}
          </div>
        )}

        <div className="field">
          <label htmlFor="lang">Target language</label>
          <select
            id="lang"
            value={lang}
            onChange={(e) => setLang(e.target.value)}
            required
          >
            {LANGS.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="value">Translation</label>
          <textarea
            id="value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Enter your translation here…"
            required
            style={{ minHeight: 100 }}
          />
        </div>

        <div className="field">
          <label htmlFor="note">
            Note <span className="text-muted">(optional)</span>
          </label>
          <input
            id="note"
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Any context or notes for the reviewer"
          />
        </div>

        <div className="row">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading || !key || !value.trim()}
          >
            {loading ? "Submitting…" : "Submit translation"}
          </button>
        </div>
      </form>
    </div>
  );
}
