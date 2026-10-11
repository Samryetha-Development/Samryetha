import { useRef, useState } from "react";
import { Dialog } from "./ui-commons";
import { useI18n } from "./lib/i18n";

function localTime(stamp: number) {
  const date = new Date(stamp);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function ScheduleDialog({ open, onOpenChange, initialAt, allowLock = false, pending, error, onConfirm }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialAt?: number;
  allowLock?: boolean;
  pending: boolean;
  error: string | null;
  onConfirm: (scheduledAt: number, canModify: boolean) => void;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState(() => localTime(initialAt ?? Date.now() + 3600_000));
  const [canModify, setCanModify] = useState(true);
  const timeInput = useRef<HTMLInputElement>(null);
  const stamp = new Date(value).getTime();
  const valid = Number.isFinite(stamp) && stamp > Date.now() && stamp <= Date.now() + 366 * 86400_000;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  return <Dialog open={open} onOpenChange={(next) => { if (!pending) onOpenChange(next); }}
    title={t(allowLock ? "schedule.title" : "schedule.changeTime")}
    description={t("schedule.timezone", { zone })} error={error} dismissible={!pending}
    actions={<><button className="edit-profile" type="button" disabled={pending} onClick={() => onOpenChange(false)}>{t("common.cancel")}</button>
      <button className="primary-action" type="button" disabled={pending || !valid} onClick={() => {
        // Read the native date control at confirmation as well: some date pickers
        // commit their change only when focus leaves the field.
        const entered = timeInput.current?.value ?? value;
        const selectedAt = new Date(entered).getTime();
        setValue(entered);
        if (Number.isFinite(selectedAt) && selectedAt > Date.now() && selectedAt <= Date.now() + 366 * 86400_000) onConfirm(selectedAt, canModify);
      }}>{t(pending ? "common.loading" : allowLock ? "schedule.confirm" : "schedule.saveTime")}</button></>}>
    <label className="form-field schedule-time"><span>{t("schedule.publishAt")}</span><input ref={timeInput} type="datetime-local" value={value} min={localTime(Date.now() + 60_000)} max={localTime(Date.now() + 366 * 86400_000)} disabled={pending} onInput={(event) => setValue(event.currentTarget.value)} onChange={(event) => setValue(event.target.value)} /></label>
    {!valid && <p className="form-error">{t("schedule.invalidTime")}</p>}
    {allowLock && <><label className="schedule-option"><input type="checkbox" checked={canModify} disabled={pending} onChange={(event) => setCanModify(event.target.checked)} /><span>{t("schedule.allowChanges")}</span></label>
      <p className={`schedule-hint ${canModify ? "" : "schedule-locked"}`}>{t(canModify ? "schedule.editableHint" : "schedule.lockedHint")}</p></>}
  </Dialog>;
}
