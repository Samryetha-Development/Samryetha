import { useEffect, useState } from "react";
import { LakoDropdown, LakoInputBox } from "@lako/ui";
import type { CatalogEntry } from "../api";
import { getCatalog } from "../api";
import { i18nLanguages } from "../languages";
import { useNotify } from "../notifications";

const LANGS = [
  { code: "", label: "Source only" },
  ...i18nLanguages.filter((language) => language.code !== "en"),
];

export default function CatalogPage() {
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [lang, setLang] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const notify = useNotify();

  useEffect(() => {
    setLoading(true);
    getCatalog(lang || undefined)
      .then(setEntries)
      .catch((e) => {
        const message = e instanceof Error ? e.message : "Failed to load strings.";
        notify(message, "error");
      })
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

  const selectedLanguage = LANGS.find((item) => item.code === lang) ?? LANGS[0];

  return (
    <div>
      <h1 className="page-title">Source Strings</h1>
      <p className="page-sub">
        All translatable strings in Samryetha. Select a language to see approved translations.
      </p>

      <div className="row" style={{ marginBottom: 20 }}>
        <LakoInputBox
          placeholder="Search keys or values…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search source strings"
          containerClassName="catalog-search"
        />
        <LakoDropdown
          className="catalog-language"
          items={LANGS}
          value={selectedLanguage}
          onChange={(item) => setLang(item.code)}
          getKey={(item) => item.code}
          getLabel={(item) => item.label}
          placeholder="Source only"
          ariaLabel="Catalog language"
          searchable
          searchPlaceholder="Search languages…"
          emptyLabel="No languages found"
          getSearchText={(item) => item.label}
        />
      </div>

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
