import { Children, isValidElement, type ReactNode } from "react";
import { LakoDropdown } from "@lako/ui/components";

type Option = { value: string; label: ReactNode; disabled?: boolean };

// Keep the file API's option values (including unknown server values) while
// presenting the same keyboard-operated dropdown as the rest of Lako UI.
export function FileSelect({ value, onValueChange, children, ariaLabel, "aria-label": ariaLabelAttribute, disabled, className }: {
  value: string | number;
  onValueChange: (value: string) => void;
  children: ReactNode;
  ariaLabel?: string;
  "aria-label"?: string;
  disabled?: boolean;
  className?: string;
}) {
  const items = Children.toArray(children).flatMap((child): Option[] => {
    if (!isValidElement<{ value: string | number; children: ReactNode; disabled?: boolean }>(child) || child.type !== "option") return [];
    return [{ value: String(child.props.value), label: child.props.children, disabled: child.props.disabled }];
  });
  return <LakoDropdown
    items={items}
    value={items.find((item) => item.value === String(value)) ?? null}
    onChange={(item) => onValueChange(item.value)}
    getKey={(item) => item.value}
    getLabel={(item) => item.label}
    getDisabled={(item) => Boolean(item.disabled)}
    ariaLabel={ariaLabel ?? ariaLabelAttribute ?? ""}
    disabled={disabled}
    className={className}
  />;
}
