import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

export type LakoTabItem<T extends string> = {
  id: T;
  label: ReactNode;
  disabled?: boolean;
};

export type LakoTabsProps<T extends string> = {
  items: LakoTabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
};

export function LakoTabs<T extends string>({ items, value, onChange, ariaLabel, className = "" }: LakoTabsProps<T>) {
  const rootRef = useRef<HTMLDivElement>(null);
  const refs = useRef(new Map<T, HTMLButtonElement>());
  const [indicator, setIndicator] = useState({ x: 0, width: 0, ready: false, animate: false });
  const enabled = items.filter((item) => !item.disabled);

  useLayoutEffect(() => {
    const root = rootRef.current;
    const active = refs.current.get(value);
    if (!root || !active) return;
    const measure = () => {
      const rootRect = root.getBoundingClientRect();
      const activeRect = active.getBoundingClientRect();
      setIndicator((current) => ({ x: activeRect.left - rootRect.left, width: activeRect.width, ready: true, animate: current.ready }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    observer.observe(active);
    return () => observer.disconnect();
  }, [value, items]);

  const move = (current: T, direction: -1 | 1) => {
    const index = enabled.findIndex((item) => item.id === current);
    const next = enabled[(index + direction + enabled.length) % enabled.length];
    if (!next) return;
    onChange(next.id);
    refs.current.get(next.id)?.focus();
  };

  return (
    <div className={`lako-ui-tabs ${className}`.trim()} role="tablist" aria-label={ariaLabel} ref={rootRef}>
      {items.map((item) => (
        <button
          className={value === item.id ? "active" : undefined}
          key={item.id}
          type="button"
          role="tab"
          ref={(node) => {
            if (node) refs.current.set(item.id, node);
            else refs.current.delete(item.id);
          }}
          tabIndex={value === item.id ? 0 : -1}
          disabled={item.disabled}
          aria-selected={value === item.id}
          onClick={() => onChange(item.id)}
          onKeyDown={(event) => {
            if (event.key === "ArrowRight" || event.key === "ArrowDown") {
              event.preventDefault();
              move(item.id, 1);
            } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
              event.preventDefault();
              move(item.id, -1);
            } else if (event.key === "Home" || event.key === "End") {
              event.preventDefault();
              const target = event.key === "Home" ? enabled[0] : enabled[enabled.length - 1];
              if (target) {
                onChange(target.id);
                refs.current.get(target.id)?.focus();
              }
            }
          }}
        >
          {item.label}
        </button>
      ))}
      <span className={`lako-ui-tabs-indicator${indicator.ready ? " ready" : ""}${indicator.animate ? " animate" : ""}`} style={{ width: indicator.width, transform: `translateX(${indicator.x}px)` }} aria-hidden="true" />
    </div>
  );
}
