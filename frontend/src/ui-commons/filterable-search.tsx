import { useState, type FormEventHandler, type ReactNode } from "react";
import { LakoButton, LakoDialog, LakoInputBox } from "@lako/ui/components";

export function FilterableSearch({ value, onChange, onSubmit, placeholder, searchLabel, filterLabel, closeLabel, clearLabel, filterCount, onClear, children }: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: FormEventHandler<HTMLFormElement>;
  placeholder: string;
  searchLabel: string;
  filterLabel: string;
  closeLabel: string;
  clearLabel: string;
  filterCount: number;
  onClear: () => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return <>
    <form className="filterable-search" onSubmit={onSubmit} role="search">
      <LakoInputBox type="search" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} aria-label={placeholder}
        prefix={<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" strokeWidth="1.5" /><path d="m16 16 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>}
        suffix={<LakoButton variant="ghost" size="sm" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>{filterLabel}{filterCount > 0 ? ` · ${filterCount}` : ""}</LakoButton>} />
      <LakoButton type="submit" variant="secondary" size="sm" className="filterable-search-submit">{searchLabel}</LakoButton>
    </form>
    <LakoDialog open={open} onOpenChange={setOpen} title={filterLabel} className="files-filter-dialog" actions={<><LakoButton variant="ghost" onClick={onClear} disabled={filterCount === 0}>{clearLabel}</LakoButton><LakoButton variant="primary" onClick={() => setOpen(false)}>{closeLabel}</LakoButton></>}>
      <div className="files-filter-fields">{children}</div>
    </LakoDialog>
  </>;
}
