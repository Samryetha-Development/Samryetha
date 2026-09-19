import { useEffect, useState } from "react";
import type { CatalogEntry } from "../api";
import { getCatalog } from "../api";

const LANGS = [
  { code: "", label: "Source only" },
  { code: "zh-Hans", label: "Chinese (Simplified)" },
  { code: "zh-Hant", label: "Chinese (Traditional)" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "es", label: "Spanish" },
  { code: "pt", label: "Portuguese" },
];

export default function CatalogPage() {
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [lang, setLang] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getCatalog(lang || undefined)
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [lang]);

  const filtered = entries.filter((e) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      e.key.toLowerCase().includes(q) ||
      e.value.toLowerCase().includes(q) ||
      (e.translation ?? "").toLowerCase().includes(q)
    );
  });

  return (
    <div>
      <h1 className="page-title">Source Strings</h1>
      <p className="page-sub">
        All translatable strings in Samryetha. Select a language to see approved translations.
      </p>

      <div className="row" style={{ marginBottom: 20 }}>
        <input
          type="text"
          placeholder="Search keys or values…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: 1 }}
        />
        <select
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          style={{ width: 220 }}
        >
          {LANGS.map((l) => (
            <option key={l.code} value={l.code}>
              {l.label}
            </option>
          ))}
        </select>
      </div>

      {error && <div className="error-banner" style={{ marginBottom: 16 }}>{error}</div>}

      {loading ? (
        <div className="empty-state">Loading strings…</div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">No strings found.</div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: "30%" }}>Key</th>
                <th>Source (English)</th>
                {lang && <th>Translation</th>}
                <th style={{ width: 70 }}>Context</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => (
                <tr key={e.key}>
                  <td>
                    <span className="mono">{e.key}</span>
                  </td>
                  <td>{e.value}</td>
                  {lang && (
                    <td>
                      {e.translation ? (
                        e.translation
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                  )}
                  <td>
                    {e.context ? (
                      <span className="text-muted text-sm">{e.context}</span>
                    ) : (
                      <span className="text-muted">—</span>
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
