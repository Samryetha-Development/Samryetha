import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export type LakoDropdownProps<T> = {
  items: T[];
  value: T | null;
  onChange: (item: T) => void;
  getKey: (item: T) => string | number;
  getLabel: (item: T) => ReactNode;
  ariaLabel: string;
  placeholder?: ReactNode;
  disabled?: boolean;
  getDisabled?: (item: T) => boolean;
  className?: string;
  searchable?: boolean;
  searchPlaceholder?: string;
  emptyLabel?: ReactNode;
  getSearchText?: (item: T) => string;
};

export function LakoDropdown<T>({
  items,
  value,
  onChange,
  getKey,
  getLabel,
  ariaLabel,
  placeholder = "Choose an option",
  disabled = false,
  getDisabled,
  className = "",
  searchable = false,
  searchPlaceholder = "Search…",
  emptyLabel = "No results",
  getSearchText,
}: LakoDropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listboxId = `lako-dropdown-${useId().replace(/:/g, "")}`;
  const selectedKey = value == null ? null : String(getKey(value));
  const filteredItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!searchable || !needle) return items;
    return items.filter((item) => String(getSearchText?.(item) ?? getLabel(item)).toLocaleLowerCase().includes(needle));
  }, [getLabel, getSearchText, items, query, searchable]);
  const selectableItems = filteredItems.filter((item) => !getDisabled?.(item));

  const close = (returnFocus = true) => {
    setOpen(false);
    setQuery("");
    setActiveIndex(-1);
    if (returnFocus) window.requestAnimationFrame(() => triggerRef.current?.focus());
  };

  const choose = (item: T) => {
    if (getDisabled?.(item)) return;
    onChange(item);
    close();
  };

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const selected = selectableItems.findIndex((item) => String(getKey(item)) === selectedKey);
    setActiveIndex(selected >= 0 ? selected : selectableItems.length ? 0 : -1);
    if (searchable) window.requestAnimationFrame(() => searchRef.current?.focus());
  }, [open, searchable]);

  useEffect(() => {
    if (!open) return;
    setActiveIndex(selectableItems.length ? 0 : -1);
  }, [query]);

  const move = (direction: -1 | 1) => {
    if (disabled || selectableItems.length === 0) return;
    const current = activeIndex < 0 ? (direction > 0 ? -1 : 0) : activeIndex;
    setActiveIndex((current + direction + selectableItems.length) % selectableItems.length);
    if (!open) setOpen(true);
  };

  const handleNavigation = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter" && open && activeIndex >= 0) {
      event.preventDefault();
      choose(selectableItems[activeIndex]);
    }
  };

  return (
    <div className={`lako-ui-dropdown ${className}`.trim()} ref={rootRef}>
      <button
        className={`lako-ui-dropdown-trigger${open ? " open" : ""}`}
        type="button"
        ref={triggerRef}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        onClick={() => open ? close(false) : setOpen(true)}
        onKeyDown={handleNavigation}
      >
        <span>{value == null ? placeholder : getLabel(value)}</span>
        <i aria-hidden="true" />
      </button>
      <div
        className={`lako-ui-dropdown-options${open ? " open" : ""}`}
        id={listboxId}
        role="listbox"
        aria-label={ariaLabel}
        aria-hidden={!open}
      >
        {searchable && open && (
          <div className="lako-ui-dropdown-search-wrap">
            <input
              ref={searchRef}
              className="lako-ui-dropdown-search"
              type="search"
              value={query}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
              aria-controls={listboxId}
              aria-activedescendant={activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleNavigation}
            />
          </div>
        )}
        {filteredItems.map((item) => {
          const key = String(getKey(item));
          const selected = selectedKey === key;
          const itemDisabled = getDisabled?.(item) ?? false;
          const selectableIndex = selectableItems.findIndex((candidate) => String(getKey(candidate)) === key);
          const active = selectableIndex >= 0 && selectableIndex === activeIndex;
          return (
            <button
              className={`lako-ui-dropdown-option${selected ? " selected" : ""}${active ? " active" : ""}`}
              id={selectableIndex >= 0 ? `${listboxId}-option-${selectableIndex}` : undefined}
              key={key}
              type="button"
              role="option"
              tabIndex={-1}
              disabled={itemDisabled}
              aria-selected={selected}
              onPointerMove={() => selectableIndex >= 0 && setActiveIndex(selectableIndex)}
              onClick={() => choose(item)}
            >
              <span>{getLabel(item)}</span>
              {selected && <i aria-hidden="true" />}
            </button>
          );
        })}
        {filteredItems.length === 0 && <div className="lako-ui-dropdown-empty">{emptyLabel}</div>}
      </div>
    </div>
  );
}
