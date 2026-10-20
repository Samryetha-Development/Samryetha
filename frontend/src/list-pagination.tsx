import { useI18n } from "./lib/i18n";

export type ListPaginationProps = {
  page: number;
  limit: number;
  loading: boolean;
  hasPrevious: boolean;
  hasNext: boolean;
  goTo: (page: number) => void;
  previous: () => void;
  next: () => void;
  changeLimit: (value: number) => void;
};

export function ListPagination({ page, loading, hasPrevious, hasNext, previous, next, goTo }: ListPaginationProps) {
  const { t } = useI18n();
  return <nav className="list-pagination" aria-label={t("pagination.label")}>
    <div className="page-navigation">
      <button type="button" className="pagination-button" disabled={loading || !hasPrevious} onClick={previous}>{t("pagination.previous")}</button>
      {page > 5 && <><button type="button" className="pagination-button" disabled={loading} onClick={() => goTo(1)}>1</button><span>…</span></>}
      {Array.from({ length: Math.min(page, 5) }, (_, i) => page - Math.min(page, 5) + i + 1).map((number) => <button key={number} type="button" className="pagination-button pagination-number" aria-current={number === page ? "page" : undefined} aria-label={t("pagination.page", { page: number })} disabled={loading} onClick={() => goTo(number)}>{number}</button>)}
      {hasNext && <button type="button" className="pagination-button pagination-number" disabled={loading} onClick={next}>{page + 1}</button>}
      <button type="button" className="pagination-button" disabled={loading || !hasNext} onClick={next}>{t("pagination.next")}</button>
    </div>
  </nav>;
}
