import { useEffect, useRef, useState, type FormEvent } from "react";
import { AppShell } from "./app-shell";
import { useAnimatedTabs } from "./lib/use-animated-tabs";
import { useTabIndicator } from "./lib/use-tab-indicator";
import { api, ApiError } from "./lib/api";
import { useAuth } from "./lib/auth";

type SettingsSection = "account" | "notifications" | "privacy" | "appearance";

const sections: { id: SettingsSection; label: string }[] = [
  { id: "account", label: "Account" },
  { id: "notifications", label: "Notifications" },
  { id: "privacy", label: "Privacy" },
  { id: "appearance", label: "Appearance" },
];

// 偏好 key → 默认值。默认值只在用户从未保存过时生效（后端 settings 是浅合并）。
type PrefKey = "show_online_status" | "notif_replies" | "notif_follows" | "notif_mentions" | "weekly_digest" | "public_profile" | "direct_messages" | "reduce_motion" | "compact_lists";
const PREF_DEFAULTS: Record<PrefKey, boolean> = {
  show_online_status: true,
  notif_replies: true,
  notif_follows: true,
  notif_mentions: true,
  weekly_digest: false,
  public_profile: true,
  direct_messages: true,
  reduce_motion: false,
  compact_lists: false,
};

function Toggle({ value, onChange, label }: { value: boolean; onChange: (value: boolean) => void; label: string }) {
  return <button className={`settings-toggle ${value ? "on" : ""}`} type="button" role="switch" aria-checked={value} aria-label={label} onClick={() => onChange(!value)}><span /></button>;
}

function SettingRow({ title, description, value, onChange }: { title: string; description: string; value: boolean; onChange: (value: boolean) => void }) {
  return <div className="setting-row"><div><h3>{title}</h3><p>{description}</p></div><Toggle label={title} value={value} onChange={onChange} /></div>;
}

export function SettingsPage() {
  const { user, refresh } = useAuth();
  const { active: selectedSection, committed: section, phase: contentPhase, setActive: switchSection } = useAnimatedTabs<SettingsSection>({ initial: "account", duration: 125 });
  const settingsNavRef = useRef<HTMLElement>(null);
  const navIndicator = useTabIndicator(settingsNavRef, (s) => `[data-settings-section="${s}"]`, selectedSection);

  const [displayName, setDisplayName] = useState("");
  const [username, setUsername] = useState("");
  const [recoveryEmail, setRecoveryEmail] = useState("");
  const [bio, setBio] = useState("");
  const [saveState, setSaveState] = useState<"" | "saving" | "saved" | "error">("");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [pwState, setPwState] = useState<"" | "saving" | "saved" | "error">("");
  const [pwMessage, setPwMessage] = useState<string | null>(null);

  // 偏好：单一 state 对象，乐观更新 + 失败回滚。persistVersion 防止旧响应覆盖新状态。
  const [prefs, setPrefs] = useState<Record<PrefKey, boolean>>(PREF_DEFAULTS);
  const persistVersion = useRef(0);

  useEffect(() => {
    if (!user) return;
    setDisplayName(user.displayName);
    setUsername(user.username);
    setRecoveryEmail(user.recoveryEmail ?? "");
    setBio(user.bio);
    setPrefs({ ...PREF_DEFAULTS, ...(user.settings as Partial<Record<PrefKey, boolean>>) });
  }, [user]);

  const persistPreference = async (patch: Partial<Record<PrefKey, boolean>>) => {
    const version = ++persistVersion.current;
    const previous: Partial<Record<PrefKey, boolean>> = {};
    for (const key of Object.keys(patch) as PrefKey[]) previous[key] = prefs[key];
    setPrefs((current) => ({ ...current, ...patch }));
    try {
      const data = await api.users.updateProfile({ settings: patch });
      if (version === persistVersion.current) {
        setPrefs((current) => ({ ...current, ...(data.user.settings as Partial<Record<PrefKey, boolean>>) }));
      }
      setSaveState("saved");
      setSaveMessage("Changes saved.");
    } catch (err) {
      if (version !== persistVersion.current) return; // 已被更新的请求接管，放弃回滚
      setPrefs((current) => ({ ...current, ...previous }));
      setSaveState("error");
      setSaveMessage(err instanceof ApiError ? err.message : "Could not save changes.");
    }
  };

  const saveProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!displayName.trim()) {
      setSaveState("error");
      setSaveMessage("Display name cannot be empty.");
      return;
    }
    if (!/^[a-z0-9_]{3,30}$/i.test(username.trim())) {
      setSaveState("error");
      setSaveMessage("Username must be 3-30 letters, numbers, or underscores.");
      return;
    }
    if (recoveryEmail.trim() && !/^\S+@\S+\.\S+$/.test(recoveryEmail.trim())) {
      setSaveState("error");
      setSaveMessage("Enter a valid recovery email.");
      return;
    }
    setSaveState("saving");
    setSaveMessage(null);
    try {
      // 未填 recovery email 时省略该字段：后端 min_length=3 会拒绝空串 ""，否则未设邮箱的用户保存任何资料都 422
      // Omit recoveryEmail when empty: backend rejects "" via min_length=3, otherwise users without it get 422 on every save
      const patch: { displayName: string; username: string; bio: string; recoveryEmail?: string } = {
        displayName: displayName.trim(),
        username: username.trim(),
        bio: bio.trim(),
      };
      if (recoveryEmail.trim()) patch.recoveryEmail = recoveryEmail.trim();
      await api.users.updateProfile(patch);
      await refresh();
      setSaveState("saved");
      setSaveMessage("Changes saved.");
    } catch (err) {
      setSaveState("error");
      setSaveMessage(err instanceof ApiError ? err.message : "Could not save changes.");
    }
  };

  const changePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!currentPassword.trim()) {
      setPwState("error");
      setPwMessage("Enter your current password.");
      return;
    }
    setPwState("saving");
    setPwMessage(null);
    try {
      await api.auth.changePassword({ currentPassword, newPassword });
      setPwState("saved");
      setPwMessage("Password updated.");
      setCurrentPassword("");
      setNewPassword("");
    } catch (err) {
      setPwState("error");
      setPwMessage(err instanceof ApiError ? err.message : "Could not change password.");
    }
  };

  return (
    <AppShell current="settings">
      <main className="shell settings-layout">
        <aside className="settings-sidebar">
          <h1>Settings</h1>
          <nav className="settings-nav" aria-label="Settings categories" ref={settingsNavRef}>
            {sections.map((item) => <button data-settings-section={item.id} className={selectedSection === item.id ? "active" : ""} key={item.id} type="button" aria-current={selectedSection === item.id ? "page" : undefined} onClick={() => switchSection(item.id)}>{item.label}</button>)}
            <span className={`settings-nav-indicator ${navIndicator.ready ? "ready" : ""}`} style={{ width: navIndicator.width, height: navIndicator.height, transform: `translate(${navIndicator.x}px, ${navIndicator.y}px)` }} aria-hidden="true" />
            <span className={`settings-nav-accent ${navIndicator.ready ? "ready" : ""}`} style={{ transform: `translate(${navIndicator.x}px, ${navIndicator.y + 10}px)` }} aria-hidden="true" />
          </nav>
        </aside>

        <section className={`settings-content ${contentPhase}`} aria-live="polite">
          {section === "account" && <>
            <header><h2>Account</h2><p>Manage the details people see across Samryetha.</p></header>
            <form className="settings-form" onSubmit={saveProfile} noValidate>
              <div className="settings-field-grid">
                <label><span>Display name</span><input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={50} /></label>
                <label><span>Username</span><div className="prefixed-input"><span>@</span><input value={username} onChange={(e) => setUsername(e.target.value)} maxLength={30} /></div></label>
                <label><span>Recovery email</span><input type="email" autoComplete="email" value={recoveryEmail} onChange={(e) => setRecoveryEmail(e.target.value)} maxLength={200} placeholder="you@example.com" /></label>
              </div>
              <label><span>Bio</span><textarea rows={4} value={bio} onChange={(e) => setBio(e.target.value)} maxLength={500} placeholder="A sentence or two about you." /></label>
              {saveMessage && <p className={`form-error ${saveState === "saved" ? "saved-note" : ""}`} role="status">{saveMessage}</p>}
              <div className="settings-actions"><button className="primary-action" type="submit" disabled={saveState === "saving"}>{saveState === "saving" ? "Saving…" : "Save changes"}</button></div>
            </form>

            <header className="settings-sub"><h2>Password</h2><p>Set a new password for your account.</p></header>
            <form className="settings-form" onSubmit={changePassword} noValidate>
              <label><span>Current password</span><input type="password" autoComplete="current-password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} /></label>
              <label><span>New password</span><input type="password" autoComplete="new-password" placeholder="At least 8 characters" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></label>
              {pwMessage && <p className={`form-error ${pwState === "saved" ? "saved-note" : ""}`} role="status">{pwMessage}</p>}
              <div className="settings-actions"><button className="primary-action" type="submit" disabled={pwState === "saving" || !currentPassword.trim() || newPassword.length < 8}>{pwState === "saving" ? "Updating…" : "Update password"}</button></div>
            </form>
          </>}

          {section === "notifications" && <>
            <header><h2>Notifications</h2><p>Choose what is worth interrupting you for.</p></header>
            <div className="settings-group">
              <SettingRow title="Mentions and replies" description="When someone mentions you or replies to your discussion." value={prefs.notif_mentions && prefs.notif_replies} onChange={(value) => { void persistPreference({ notif_mentions: value, notif_replies: value }); }} />
              <SettingRow title="New followers" description="When someone starts following your profile." value={prefs.notif_follows} onChange={(value) => { void persistPreference({ notif_follows: value }); }} />
              <SettingRow title="Weekly digest" description="A quiet summary of discussions you may have missed." value={prefs.weekly_digest} onChange={(value) => { void persistPreference({ weekly_digest: value }); }} />
            </div>
            <p className="community-note">These preferences are saved to your account. Push routing based on them lands later.</p>
          </>}

          {section === "privacy" && <>
            <header><h2>Privacy</h2><p>Control how other people can find and contact you.</p></header>
            <div className="settings-group">
              <SettingRow title="Public profile" description="Let anyone on campus view your profile and activity." value={prefs.public_profile} onChange={(value) => { void persistPreference({ public_profile: value }); }} />
              <SettingRow title="Show online status" description="Show when you are currently active." value={prefs.show_online_status} onChange={(value) => { void persistPreference({ show_online_status: value }); }} />
              <SettingRow title="Direct messages" description="Allow other students to send you private messages." value={prefs.direct_messages} onChange={(value) => { void persistPreference({ direct_messages: value }); }} />
            </div>
            <p className="community-note">These preferences are saved to your account.</p>
          </>}

          {section === "appearance" && <>
            <header><h2>Appearance</h2><p>Adjust how Samryetha looks and feels on this device.</p></header>
            <div className="settings-group">
              <SettingRow title="Reduce motion" description="Minimize page and tab transition animations." value={prefs.reduce_motion} onChange={(value) => { void persistPreference({ reduce_motion: value }); }} />
              <SettingRow title="Compact lists" description="Fit more discussions on screen at once." value={prefs.compact_lists} onChange={(value) => { void persistPreference({ compact_lists: value }); }} />
            </div>
          </>}
        </section>
      </main>
    </AppShell>
  );
}
