import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError, type PollResponse } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useI18n } from "./lib/i18n";

export function PollCard({ discussionId, poll, locked, onChange, onSignIn }: {
  discussionId: number;
  poll: PollResponse;
  locked: boolean;
  onChange: (poll: PollResponse | null, locked?: boolean) => void;
  onSignIn: () => void;
}) {
  const { user } = useAuth();
  const { t } = useI18n();
  const id = useId();
  const [selected, setSelected] = useState(poll.viewerOptionIds);
  const [busy, setBusy] = useState(true);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const inFlight = useRef(false);
  const requestGeneration = useRef(0);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const tRef = useRef(t);
  tRef.current = t;
  useEffect(() => {
    // The parent detail can belong to the previous session, even after a keyed remount.
    const generation = ++requestGeneration.current;
    inFlight.current = true;
    setBusy(true); setReady(false); setSelected([]); setSaved(false); setError(null);
    void api.discussions.get(discussionId).then((result) => {
      if (generation !== requestGeneration.current) return;
      onChangeRef.current(result.poll ?? null, result.isLocked);
      setSelected(result.poll?.viewerOptionIds ?? []);
      setReady(true);
    }).catch(() => {
      if (generation === requestGeneration.current) setError(tRef.current("poll.refreshFail"));
    }).finally(() => {
      if (generation !== requestGeneration.current) return;
      inFlight.current = false;
      setBusy(false);
    });
    return () => { requestGeneration.current++; inFlight.current = false; };
  }, [discussionId, user?.id, user?.status]);
  useEffect(() => {
    if (!ready) return;
    setSelected(poll.viewerOptionIds); setError(null);
  }, [ready, discussionId, user?.id, poll.viewerOptionIds.join(","), poll.options.map((option) => option.id).join(",")]);
  const changed = selected.length !== poll.viewerOptionIds.length || selected.some((option) => !poll.viewerOptionIds.includes(option));
  const vote = async () => {
    if (!user) { onSignIn(); return; }
    if (inFlight.current || !ready || !poll.canVote || locked || !selected.length || !changed) return;
    const generation = ++requestGeneration.current;
    inFlight.current = true;
    setBusy(true); setError(null); setSaved(false);
    try {
      const result = await api.discussions.vote(discussionId, selected);
      if (generation === requestGeneration.current) { onChangeRef.current(result); setSelected(result.viewerOptionIds); setSaved(true); }
    } catch (err) {
      if (generation === requestGeneration.current) setError(err instanceof ApiError ? err.message : t("poll.voteFail"));
    } finally {
      if (generation === requestGeneration.current) { inFlight.current = false; setBusy(false); }
    }
  };
  const refresh = async () => {
    if (inFlight.current) return;
    const generation = ++requestGeneration.current;
    inFlight.current = true; setBusy(true); setError(null);
    try {
      const result = await api.discussions.get(discussionId);
      if (generation === requestGeneration.current) {
        onChangeRef.current(result.poll ?? null, result.isLocked);
        setSelected(result.poll?.viewerOptionIds ?? []);
        setReady(true);
      }
    } catch {
      if (generation === requestGeneration.current) setError(t("poll.refreshFail"));
    } finally {
      if (generation === requestGeneration.current) { inFlight.current = false; setBusy(false); }
    }
  };
  return <section className="poll-card" aria-labelledby={id} aria-busy={busy}>
    <div className="poll-card-heading"><span className="poll-eyebrow">{t("poll.heading")} · {t(poll.allowMultiple ? "poll.multiple" : "poll.single")}</span>
      <button type="button" className="draft-action" onClick={() => void refresh()} disabled={busy}>{t("poll.refresh")}</button>
    </div>
    <h2 id={id}>{poll.question}</h2>
    <fieldset className="poll-choices" disabled={busy || !ready || !poll.canVote || locked}>
      <legend className="sr-only">{t(poll.allowMultiple ? "poll.chooseMultiple" : "poll.chooseSingle")}</legend>
      {poll.options.map((option) => {
        const percent = poll.totalVoters ? Math.round(option.voteCount / poll.totalVoters * 100) : 0;
        return <label className={`poll-choice${selected.includes(option.id) ? " selected" : ""}`} key={option.id}>
          <span className="poll-result-bar" aria-hidden="true" style={{ width: `${percent}%` }} />
          <input type={poll.allowMultiple ? "checkbox" : "radio"} name={id} checked={selected.includes(option.id)} onChange={() => {
            setSaved(false);
            setSelected((current) => poll.allowMultiple ? current.includes(option.id) ? current.filter((item) => item !== option.id) : [...current, option.id] : [option.id]);
          }} />
          <span className="poll-choice-label">{option.label}</span>
          <span className="poll-choice-stat">{t("poll.optionStats", { count: option.voteCount, percent })}</span>
        </label>;
      })}
    </fieldset>
    <div className="poll-footer"><span className="poll-totals">{t("poll.total", { voters: poll.totalVoters, votes: poll.totalVotes })}</span>
      <button type="button" className="primary-action" onClick={() => void vote()} disabled={busy || locked || Boolean(user && (!ready || !poll.canVote || !selected.length || !changed))}>
        {t(!user ? "poll.signIn" : busy ? "poll.submitting" : poll.viewerOptionIds.length ? "poll.updateVote" : "poll.vote")}
      </button>
    </div>
    {poll.allowMultiple && <p className="poll-hint">{t("poll.percentHint")}</p>}
    {locked && <p className="poll-hint">{t("poll.closed")}</p>}
    {ready && user && !locked && !poll.canVote && <p className="poll-hint">{t("poll.activeRequired")}</p>}
    {saved && <p className="poll-hint" role="status">{t("poll.saved")}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}
