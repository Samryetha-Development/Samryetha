import { useEffect, useId, useState } from "react";
import { useI18n } from "./lib/i18n";

export type ListPaginationProps = {
  page: number;
  limit: number;
  loading: boolean;
  hasPrevious: boolean;
  hasNext: boolean;
  previous: () => void;
  next: () => void;
  changeLimit: (value: number) => void;
};

export function ListPagination({ page, limit, loading, hasPrevious, hasNext, previous, next, changeLimit }: ListPaginationProps) {
  const { t } = useI18n();
  const id = useId();
  const [value, setValue] = useState(String(limit));
  useEffect(() => setValue(String(limit)), [limit]);
  return <nav className="list-pagination" aria-label={t("pagination.label")}>
    <form className="page-size" onSubmit={(event) => {
      event.preventDefault();
      changeLimit(Number(value));
    }}>
      <label htmlFor={id}>{t("pagination.perPage")}</label>
      <input id={id} type="number" min="1" max="50" step="1" required value={value} onChange={(event) => setValue(event.target.value)} />
      <button type="submit" className="action-btn" disabled={loading || Number(value) === limit}>{t("pagination.apply")}</button>
    </form>
    <div className="page-navigation">
      <button type="button" className="action-btn" disabled={loading || !hasPrevious} onClick={previous}>{t("pagination.previous")}</button>
      <span role="status" aria-live="polite">{t("pagination.page", { page })}</span>
      <button type="button" className="action-btn" disabled={loading || !hasNext} onClick={next}>{t("pagination.next")}</button>
    </div>
  </nav>;
}
