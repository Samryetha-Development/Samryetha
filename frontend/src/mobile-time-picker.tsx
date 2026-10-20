import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { LakoButton } from "@lako/ui/components";
import { useI18n } from "./lib/i18n";

function TimePanel({ label, value, open, onOpenChange, children }: { label: string; value: string; open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode | ((open: boolean) => ReactNode) }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open && body.current?.contains(document.activeElement)) trigger.current?.focus({ preventScroll: true });
  }, [open]);
  const id = useId();
  return <section className={`mobile-time-panel${open ? " is-open" : ""}`}>
    <button type="button" ref={trigger} className="mobile-time-panel-trigger" aria-expanded={open} aria-controls={id} onClick={() => onOpenChange(!open)}>
      <span>{label}</span><strong>{value}</strong>
    </button>
    <div ref={body} id={id} className="mobile-time-panel-body" inert={!open}>
      <div className="mobile-time-panel-inner">{typeof children === "function" ? children(open) : children}</div>
    </div>
  </section>;
}

function TimeWheel({ value, count, label, active, onChange }: { value: number; count: number; label: string; active: boolean; onChange: (value: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initializing = useRef(true);
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => {
    if (!active) return;
    initializing.current = true;
    const node = ref.current;
    if (node) node.scrollTop = (count * 3 + value) * 36;
    let frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => { initializing.current = false; }); });
    return () => { cancelAnimationFrame(frame); if (timer.current) clearTimeout(timer.current); };
  }, [count, active]);
  return <div className="time-wheel" role="spinbutton" tabIndex={0} aria-label={label} aria-valuemin={0} aria-valuemax={count - 1} aria-valuenow={value} aria-valuetext={String(value).padStart(2, "0")}
    onKeyDown={(event) => {
      const delta = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
      if (!delta && event.key !== "Home" && event.key !== "End") return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? count - 1 : (value + delta + count) % count;
      latest.current(next);
      const node = ref.current;
      if (node) {
        const currentIndex = Math.round(node.scrollTop / 36);
        const targetIndex = event.key === "Home" || event.key === "End"
          ? Math.floor(currentIndex / count) * count + next
          : currentIndex + delta;
        node.scrollTo({ top: targetIndex * 36, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.reduceMotion === "true" ? "instant" : "smooth" });
      }
    }}>
    <div className="time-wheel-scroll" ref={ref} aria-hidden="true" onScroll={() => {
      if (!active || initializing.current) return;
      if (timer.current) clearTimeout(timer.current);
      if (ref.current) latest.current(Math.round(ref.current.scrollTop / 36) % count);
      timer.current = setTimeout(() => {
        const node = ref.current;
        if (!node) return;
        const index = Math.round(node.scrollTop / 36);
        const selected = ((index % count) + count) % count;
        latest.current(selected);
        if (index < count * 2 || index >= count * 5) node.scrollTo({ top: (count * 3 + selected) * 36, behavior: "instant" });
      }, 100);
    }}>
      {Array.from({ length: count * 7 }, (_, index) => <div className="time-wheel-item" key={index} onClick={() => ref.current?.scrollTo({ top: index * 36, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.reduceMotion === "true" ? "instant" : "smooth" })}>{String(index % count).padStart(2, "0")}</div>)}
    </div>
    <span className="time-wheel-selection" aria-hidden="true" />
  </div>;
}

export function MobileTimeFields({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const { t, locale } = useI18n();
  const [dateOpen, setDateOpen] = useState(true);
  const [timeOpen, setTimeOpen] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const touching = useRef(false);
  const timeChanged = useRef(false);
  const cancelClose = () => { if (closeTimer.current) clearTimeout(closeTimer.current); };
  const closeWhenSettled = () => {
    cancelClose();
    if (!touching.current && timeChanged.current) closeTimer.current = setTimeout(() => setTimeOpen(false), 900);
  };
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);
  const date = new Date(value);
  const [month, setMonth] = useState(() => new Date(new Date(value).getFullYear(), new Date(value).getMonth(), 1));
  const picked = (update: (next: Date) => void) => { const next = new Date(value); next.setSeconds(0, 0); update(next); onChange(next.getTime()); };
  const year = month.getFullYear();
  const monthIndex = month.getMonth();
  const offset = (new Date(year, monthIndex, 1).getDay() + 6) % 7;
  const days = new Date(year, monthIndex + 1, 0).getDate();
  return (
    <div className="mobile-time-panels">
      <TimePanel label={t("schedule.chooseDate")} value={new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(date)} open={dateOpen} onOpenChange={setDateOpen}>
        <div className="mobile-calendar">
          <div className="mobile-calendar-heading"><LakoButton variant="ghost" aria-label={t("schedule.previousMonth")} onClick={() => setMonth(new Date(year, monthIndex - 1, 1))}>‹</LakoButton><span>{new Intl.DateTimeFormat(locale, { year: "numeric", month: "long" }).format(month)}</span><LakoButton variant="ghost" aria-label={t("schedule.nextMonth")} onClick={() => setMonth(new Date(year, monthIndex + 1, 1))}>›</LakoButton></div>
          <div className="mobile-calendar-grid">
            {Array.from({ length: 7 }, (_, i) => <span className="mobile-calendar-weekday" key={`weekday-${i}`}>{new Intl.DateTimeFormat(locale, { weekday: "narrow" }).format(new Date(2026, 0, 5 + i))}</span>)}
            {Array.from({ length: offset }, (_, i) => <span key={`empty-${i}`} />)}
            {Array.from({ length: days }, (_, i) => <button type="button" key={i} aria-label={new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(new Date(year, monthIndex, i + 1))} aria-pressed={date.getFullYear() === year && date.getMonth() === monthIndex && date.getDate() === i + 1} onClick={() => { picked((next) => { next.setFullYear(year, monthIndex, i + 1); }); setDateOpen(false); }}>{i + 1}</button>)}
          </div>
        </div>
      </TimePanel>
      <TimePanel label={t("schedule.time24")} value={`${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`} open={timeOpen} onOpenChange={(open) => { cancelClose(); timeChanged.current = false; setTimeOpen(open); }}>
        {(expanded) => <><div className="mobile-time-wheel-labels"><span>{t("schedule.hour")}</span><span>{t("schedule.minute")}</span></div>
        <div className="mobile-time-wheels" onPointerDownCapture={() => { touching.current = true; cancelClose(); }} onPointerUpCapture={() => { touching.current = false; closeWhenSettled(); }} onPointerCancelCapture={() => { touching.current = false; closeWhenSettled(); }}><TimeWheel active={expanded} value={date.getHours()} count={24} label={t("schedule.hour")} onChange={(hour) => { picked((next) => next.setHours(hour)); timeChanged.current = true; closeWhenSettled(); }} /><span className="mobile-time-colon" aria-hidden="true">:</span><TimeWheel active={expanded} value={date.getMinutes()} count={60} label={t("schedule.minute")} onChange={(minute) => { picked((next) => next.setMinutes(minute)); timeChanged.current = true; closeWhenSettled(); }} /></div></>}
      </TimePanel>
    </div>
  );
}
