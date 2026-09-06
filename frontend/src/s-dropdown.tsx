import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";

type SDropdownProps<T> = {
  items: T[];
  value: T | null;
  onChange: (item: T) => void;
  getKey: (item: T) => string | number;
  getLabel: (item: T) => ReactNode;
  placeholder?: ReactNode;
  ariaLabel: string;
  label?: ReactNode;
  className?: string;
  disabled?: boolean;
  getDisabled?: (item: T) => boolean;
};

export function SDropdown<T>({
  items,
  value,
  onChange,
  getKey,
  getLabel,
  placeholder = "Choose an option",
  ariaLabel,
  label,
  className = "",
  disabled = false,
  getDisabled,
}: SDropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listboxId = `s-dropdown-${useId().replace(/:/g, "")}`;

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const selectableItems = items.filter((item) => !getDisabled?.(item));
  const move = (direction: -1 | 1) => {
    if (disabled || selectableItems.length === 0) return;
    const current = value ? selectableItems.findIndex((item) => String(getKey(item)) === String(getKey(value))) : -1;
    const nextIndex = (current + direction + selectableItems.length) % selectableItems.length;
    onChange(selectableItems[nextIndex]);
    setOpen(true);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1);
    }
  };

  const selectedKey = value == null ? null : String(getKey(value));
  return (
    <div className={`form-field compact-field board-picker ${className}`} ref={pickerRef}>
      {label ? <span className="dropdown-label">{label}</span> : <span className="sr-only">{ariaLabel}</span>}
      <button
        className={`board-select ${open ? "open" : ""}`}
        type="button"
        ref={triggerRef}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={handleKeyDown}
      >
        <span>{value == null ? placeholder : getLabel(value)}</span>
        <span className="board-chevron" aria-hidden="true" />
      </button>
      <div className={`board-options ${open ? "open" : ""}`} id={listboxId} role="listbox" aria-label={ariaLabel} aria-hidden={!open}>
        {items.map((item) => {
          const itemKey = String(getKey(item));
          const selected = selectedKey === itemKey;
          const itemDisabled = getDisabled?.(item) ?? false;
          return (
            <button
              className={`board-option ${selected ? "selected" : ""}`}
              key={itemKey}
              type="button"
              role="option"
              tabIndex={open && !itemDisabled ? 0 : -1}
              disabled={itemDisabled}
              aria-selected={selected}
              aria-disabled={itemDisabled || undefined}
              onClick={() => {
                if (itemDisabled) return;
                onChange(item);
                setOpen(false);
              }}
            >
              <span>{getLabel(item)}</span>
              {selected && <span className="board-option-mark" aria-hidden="true" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
