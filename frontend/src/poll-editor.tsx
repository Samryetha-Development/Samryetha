import { useId } from "react";
import { useI18n, type I18nKey } from "./lib/i18n";

export type PollComposition = { question: string; allowMultiple: boolean; options: string[] };

export function pollError(poll: PollComposition | null): I18nKey | null {
  if (!poll) return null;
  if (!poll.question.trim() || poll.options.some((option) => !option.trim())) return "poll.incomplete";
  if (new Set(poll.options.map((option) => option.trim().toLowerCase())).size !== poll.options.length) return "poll.duplicates";
  return null;
}

export function PollToggle({ value, onChange, disabled }: {
  value: PollComposition | null;
  onChange: (value: PollComposition | null) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return <button type="button" className="editor-preview-toggle poll-toggle" aria-pressed={Boolean(value)} disabled={disabled}
    onClick={() => onChange(value ? null : { question: "", allowMultiple: false, options: ["", ""] })}>
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="M3 13V8m5 5V3m5 10V6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
    {t(value ? "poll.remove" : "poll.add")}
  </button>;
}

export function PollFields({ value, onChange, disabled, immutable }: {
  value: PollComposition | null;
  onChange: (value: PollComposition | null) => void;
  disabled?: boolean;
  immutable?: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  if (!value) return null;
  return <fieldset className="poll-editor" disabled={disabled || immutable}>
    <legend>{t("poll.heading")}</legend>
    <label className="form-field"><span>{t("poll.question")}</span>
      <input value={value.question} maxLength={200} placeholder={t("poll.questionPlaceholder")} onChange={(event) => onChange({ ...value, question: event.target.value })} />
    </label>
    <div className="poll-mode" role="group" aria-label={t("poll.mode")}>
      <label><input type="radio" name={id} checked={!value.allowMultiple} onChange={() => onChange({ ...value, allowMultiple: false })} />{t("poll.single")}</label>
      <label><input type="radio" name={id} checked={value.allowMultiple} onChange={() => onChange({ ...value, allowMultiple: true })} />{t("poll.multiple")}</label>
    </div>
    <div className="poll-option-inputs">{value.options.map((option, index) => <div className="poll-option-input" key={index}>
      <label className="form-field"><span className="sr-only">{t("poll.option", { number: index + 1 })}</span>
        <input value={option} maxLength={100} placeholder={t("poll.option", { number: index + 1 })} onChange={(event) => onChange({ ...value, options: value.options.map((item, position) => position === index ? event.target.value : item) })} />
      </label>
      <button type="button" className="draft-action" aria-label={t("poll.removeOption", { number: index + 1 })} disabled={disabled || immutable || value.options.length <= 2}
        onClick={() => onChange({ ...value, options: value.options.filter((_, position) => position !== index) })}>×</button>
    </div>)}</div>
    <button type="button" className="draft-action" disabled={disabled || immutable || value.options.length >= 10} onClick={() => onChange({ ...value, options: [...value.options, ""] })}>{t("poll.addOption")}</button>
    <p className="poll-hint">{t(immutable ? "poll.immutable" : "poll.editorHint")}</p>
  </fieldset>;
}
