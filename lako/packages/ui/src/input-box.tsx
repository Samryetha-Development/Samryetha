import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";

export type LakoInputBoxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "size"> & {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  prefix?: ReactNode;
  suffix?: ReactNode;
  containerClassName?: string;
};

export const LakoInputBox = forwardRef<HTMLInputElement, LakoInputBoxProps>(function LakoInputBox(
  { label, hint, error, prefix, suffix, containerClassName = "", id, className = "", ...props },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? `lako-input-${generatedId.replace(/:/g, "")}`;
  const helpId = `${inputId}-help`;
  return (
    <label className={`lako-ui-input-box ${containerClassName}`.trim()} htmlFor={inputId}>
      {label && <span className="lako-ui-input-label">{label}</span>}
      <span className={`lako-ui-input-control${error ? " invalid" : ""}`}>
        {prefix && <span className="lako-ui-input-affix">{prefix}</span>}
        <input
          {...props}
          className={className}
          id={inputId}
          ref={ref}
          aria-invalid={error ? true : props["aria-invalid"]}
          aria-describedby={hint || error ? helpId : props["aria-describedby"]}
        />
        {suffix && <span className="lako-ui-input-affix">{suffix}</span>}
      </span>
      {(error || hint) && <span className={`lako-ui-input-help${error ? " error" : ""}`} id={helpId}>{error ?? hint}</span>}
    </label>
  );
});
