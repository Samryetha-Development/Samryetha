import { DismissableLayer } from "@radix-ui/react-dismissable-layer";
import { useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";

import { cx } from "./cx.js";

export type DropdownProps<T> = {
  items: T[];
  value: T | null;
  onChange: (item: T) => void;
  getKey: (item: T) => string | number;
  getLabel: (item: T) => ReactNode;
  /**
   * 必填：组件不依赖宿主的 i18n，由调用方传入已翻译好的文案。
   * （早先这里读 useI18n()，是把组件库和宿主绑死的那一处。）
   */
  placeholder: ReactNode;
  ariaLabel: string;
  label?: ReactNode;
  className?: string;
  disabled?: boolean;
  getDisabled?: (item: T) => boolean;
};

/**
 * 泛型 listbox。刻意**不** portal：它是 z-index 50 的定位元素，
 * portal 化会丢掉「在 dialog 内部仍压在遮罩之上」这个行为。
 *
 * 关闭语义交给 Radix DismissableLayer（点击外部 / Esc），并用 asChild 挂在
 * .board-picker 自己身上，所以 DOM 与手写时期完全一致，换掉的只是那两段监听。
 */
export function Dropdown<T>({ items, value, onChange, getKey, getLabel, placeholder, ariaLabel, label, className, disabled = false, getDisabled }: DropdownProps<T>) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listboxId = `s-dropdown-${useId().replace(/:/g, "")}`;

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
  const picker = (
    <div className={cx("form-field", "compact-field", "board-picker", className)}>
      {label ? <span className="dropdown-label">{label}</span> : <span className="sr-only">{ariaLabel}</span>}
      <button
        className={cx("board-select", open && "open")}
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
      <div className={cx("board-options", open && "open")} id={listboxId} role="listbox" aria-label={ariaLabel} aria-hidden={!open}>
        {items.map((item) => {
          const itemKey = String(getKey(item));
          const selected = selectedKey === itemKey;
          const itemDisabled = getDisabled?.(item) ?? false;
          return (
            <button
              className={cx("board-option", selected && "selected")}
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

  // 只在展开时挂载：否则 Esc 会在下拉没打开时也把焦点抢到触发按钮上。
  if (!open) return picker;
  return (
    <DismissableLayer
      asChild
      onPointerDownOutside={() => setOpen(false)}
      onEscapeKeyDown={(event) => {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }}
    >
      {picker}
    </DismissableLayer>
  );
}
