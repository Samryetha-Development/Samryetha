import { useEffect, useState, FormEvent } from "react";
import { LakoDropdown } from "@lako/ui";
import type { CatalogEntry, User } from "../api";
import { getCatalog, submitTranslation } from "../api";
import { translationLanguages } from "../languages";
import { useNotify } from "../notifications";

const LANGS = translationLanguages;

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
  const notify = useNotify();

  useEffect(() => {
    getCatalog().then(setEntries).catch(() => null);
  }, []);

  const selectedEntry = entries.find((e) => e.key === key);
  const selectedLanguage = LANGS.find((item) => item.code === lang) ?? LANGS[0];

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!key || !lang || !value.trim()) return;
    setLoading(true);
    try {
      await submitTranslation({ key, locale: lang, value: value.trim(), note: note.trim() || undefined });
      notify("Translation submitted.", "success");
      setValue("");
      setNote("");
      setTimeout(() => {
        onSubmitted();
      }, 1500);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Submission failed";
      notify(message, "error");
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
        <div className="field">
          <label>Source string</label>
          <LakoDropdown
            items={entries}
            value={selectedEntry ?? null}
            onChange={(item) => setKey(item.key)}
            getKey={(item) => item.key}
            getLabel={(item) => `[${item.key}] ${item.value.slice(0, 60)}${item.value.length > 60 ? "…" : ""}`}
            placeholder="Select a string…"
            ariaLabel="Source string"
            searchable
            searchPlaceholder="Search source strings…"
            emptyLabel="No strings found"
            getSearchText={(item) => `${item.key} ${item.value}`}
          />
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
          <label>Target language</label>
          <LakoDropdown
            items={LANGS}
            value={selectedLanguage}
            onChange={(item) => setLang(item.code)}
            getKey={(item) => item.code}
            getLabel={(item) => item.label}
            placeholder="Select a language…"
            ariaLabel="Target language"
            searchable
            searchPlaceholder="Search languages…"
            getSearchText={(item) => item.label}
          />
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
