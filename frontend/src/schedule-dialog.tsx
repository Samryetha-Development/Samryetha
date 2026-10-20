import { useDialogExit } from "./lib/use-dialog-exit";
import { MobileTimeFields } from "./mobile-time-picker";
import { LakoButton, LakoTimeDialog } from "@lako/ui/components";
import "@lako/ui/ui.css";
import { useState, useSyncExternalStore } from "react";
import { Dialog } from "./ui-commons";
import { useI18n } from "./lib/i18n";

const subscribeMobile = (notify: () => void) => {
  const media = window.matchMedia("(max-width: 600px)");
  media.addEventListener("change", notify);
  return () => media.removeEventListener("change", notify);
};

export function ScheduleDialog({ open, onOpenChange: onParentOpenChange, initialAt, allowLock = false, pending, error, onConfirm }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialAt?: number;
  allowLock?: boolean;
  pending: boolean;
  error: string | null;
  onConfirm: (scheduledAt: number, canModify: boolean) => void;
}) {
  const { t, locale } = useI18n();
  const { visible, close } = useDialogExit(() => onParentOpenChange(false));
  const onOpenChange = (next: boolean) => { if (!next) close(); };
  const mobile = useSyncExternalStore(subscribeMobile, () => window.matchMedia("(max-width: 600px)").matches, () => false);
  const [stamp, setStamp] = useState(() => initialAt ?? Date.now() + 3600_000);
  const [choosingTime, setChoosingTime] = useState(false);
  const [canModify, setCanModify] = useState(true);
  const valid = Number.isFinite(stamp) && stamp > Date.now() && stamp <= Date.now() + 366 * 86400_000;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  return <><Dialog open={open && visible && (mobile || !choosingTime)} onOpenChange={(next) => { if (!pending) onOpenChange(next); }}
    contentClassName="schedule-dialog"
    title={t(allowLock ? "schedule.title" : "schedule.changeTime")}
    description={t("schedule.timezone", { zone })} error={error} dismissible={!pending}
    actions={<><button className="edit-profile" type="button" disabled={pending} onClick={() => onOpenChange(false)}>{t("common.cancel")}</button>
      <button className="primary-action" type="button" disabled={pending || !valid} onClick={() => {
        if (valid) onConfirm(stamp, canModify);
      }}>{t(pending ? "common.loading" : allowLock ? "schedule.confirm" : "schedule.saveTime")}</button></>}>
    {mobile ? <MobileTimeFields value={stamp} onChange={setStamp} /> : <div className="form-field schedule-time"><span>{t("schedule.publishAt")}</span>
      <LakoButton variant="secondary" disabled={pending} aria-haspopup="dialog" onClick={() => setChoosingTime(true)}>
        {new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone: zone }).format(stamp)}
      </LakoButton>
    </div>}
    {!valid && <p className="form-error">{t("schedule.invalidTime")}</p>}
    {allowLock && <><label className="schedule-option"><input type="checkbox" checked={canModify} disabled={pending} onChange={(event) => setCanModify(event.target.checked)} /><span>{t("schedule.allowChanges")}</span></label>
      <p className={`schedule-hint ${canModify ? "" : "schedule-locked"}`}>{t(canModify ? "schedule.editableHint" : "schedule.lockedHint")}</p></>}
  </Dialog>
    {!mobile && <LakoTimeDialog open={open && visible && choosingTime} onOpenChange={setChoosingTime}
      title={t("schedule.publishAt")} value={new Date(stamp).toISOString()} timeZone={zone} locale={locale}
      onConfirm={(instant) => {
        const selected = new Date(instant).getTime();
        if (!Number.isFinite(selected) || selected <= Date.now() || selected > Date.now() + 366 * 86400_000) throw new Error(t("schedule.invalidTime"));
        setStamp(selected);
      }}
      labels={{ cancel: t("common.cancel"), confirm: t("schedule.saveTime"),
        previousMonth: t("schedule.previousMonth"), nextMonth: t("schedule.nextMonth"),
        previousYear: t("schedule.previousYear"), nextYear: t("schedule.nextYear"),
        previousYears: t("schedule.previousYears"), nextYears: t("schedule.nextYears"),
        chooseMonth: t("schedule.chooseMonth"), chooseYear: t("schedule.chooseYear"),
        chooseDate: t("schedule.chooseDate"), today: t("schedule.today"),
        time: t("schedule.time24"), hour: t("schedule.hour"), minute: t("schedule.minute"), invalidTime: t("schedule.invalidTime") }} />}
  </>;
}
