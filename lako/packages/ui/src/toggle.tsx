export type LakoToggleProps = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  ariaLabel: string;
  disabled?: boolean;
  className?: string;
};

export function LakoToggle({ checked, onChange, ariaLabel, disabled = false, className = "" }: LakoToggleProps) {
  return (
    <button
      className={`lako-ui-toggle${checked ? " on" : ""} ${className}`.trim()}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span aria-hidden="true" />
    </button>
  );
}
