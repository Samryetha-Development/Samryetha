import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { UserMenu } from "./user-menu";
import { MobileMenu } from "./mobile-menu";
import { Loading } from "./loading";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type AdminStats, type AdminUser, type BoardSummary, type BoardVisibility, type DeletedDiscussion, type DeletedReply, type FeedbackApiKey, type FeedbackBackupInfo, type FeedbackBackupSettings, type FeedbackProjectAdmin, type FeedbackProjectMember, type ModerationAction, type ReportDTO, type UserRole, type UserStatus } from "./lib/api";
import { useAuth } from "./lib/auth";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";
import { useEscapeKey, useModalScrollLock } from "./lib/use-modal-scroll-lock";

type AdminSection = "dashboard" | "users" | "boards" | "moderation" | "audit" | "feedback";

const sectionKeys: { id: AdminSection; labelKey: I18nKey }[] = [
  { id: "dashboard", labelKey: "adm.dashboard" },
  { id: "users", labelKey: "adm.users" },
  { id: "boards", labelKey: "adm.boards" },
  { id: "moderation", labelKey: "adm.moderation" },
  { id: "audit", labelKey: "adm.audit" },
  { id: "feedback", labelKey: "adm.feedback" },
];

const statusKeys: { key: UserStatus | "all"; labelKey: I18nKey }[] = [
  { key: "all", labelKey: "adm.all" },
  { key: "pending", labelKey: "adm.pending" },
  { key: "active", labelKey: "adm.active" },
  { key: "banned", labelKey: "adm.banned" },
  { key: "deactivated", labelKey: "adm.deactivated" },
];

const roleKeys: { key: UserRole | "all"; labelKey: I18nKey }[] = [
  { key: "all", labelKey: "adm.allRoles" },
  { key: "student", labelKey: "adm.student" },
  { key: "admin", labelKey: "adm.admin" },
];

const visKeys: Record<BoardVisibility, I18nKey> = { public: "adm.visPublic", members: "adm.visMembers", private: "adm.visPrivate" };
const postingKeys: Record<"everyone" | "members" | "moderators", I18nKey> = { everyone: "adm.postEveryone", members: "adm.postMembers", moderators: "adm.postModerators" };
const userStatusKeys: Record<UserStatus, I18nKey> = { pending: "adm.pending", active: "adm.active", banned: "adm.banned", deactivated: "adm.deactivated" };

function SearchIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.7" /><path d="M16 16L21 21" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
}

function Badge({ children, variant }: { children: React.ReactNode; variant: string }) {
  return <span className={`admin-badge ${variant}`}>{children}</span>;
}

export type NotifyFn = (message: string, tone?: "success" | "error") => void;

export function AdminPage({ onNotify }: { onNotify: NotifyFn }) {
  const { user, loading } = useAuth();
  const { t } = useI18n();
  const [section, setSection] = useState<AdminSection>("dashboard");
  const [selectedSection, setSelectedSection] = useState<AdminSection>("dashboard");
  const [contentPhase, setContentPhase] = useState<"" | "is-leaving" | "is-entering">("");
  const [navIndicator, setNavIndicator] = useState({ width: 0, height: 0, x: 0, y: 0, ready: false });
  const adminNavRef = useRef<HTMLElement>(null);
  const transitionToken = useRef(0);
  const transitionTimer = useRef<number | null>(null);
  const transitionFrame = useRef<number | null>(null);

  useEffect(() => () => {
    if (transitionTimer.current !== null) window.clearTimeout(transitionTimer.current);
    if (transitionFrame.current !== null) window.cancelAnimationFrame(transitionFrame.current);
  }, []);

  // SSR-safe：首帧恒为 dashboard，挂载后再从 ?section= 切入（避免 hydration mismatch）
  useEffect(() => {
    if (typeof window === "undefined") return;
    const s = new URLSearchParams(window.location.search).get("section");
    if (s === "users" || s === "boards" || s === "moderation" || s === "audit" || s === "feedback") {
      setSelectedSection(s);
      setSection(s);
    }
  }, []);

  useLayoutEffect(() => {
    const moveIndicator = () => {
      const activeButton = adminNavRef.current?.querySelector<HTMLElement>(`[data-admin-section="${selectedSection}"]`);
      if (activeButton) setNavIndicator({ width: activeButton.offsetWidth, height: activeButton.offsetHeight, x: activeButton.offsetLeft, y: activeButton.offsetTop, ready: true });
    };
    moveIndicator();
    window.addEventListener("resize", moveIndicator);
    return () => window.removeEventListener("resize", moveIndicator);
  }, [selectedSection]);

  const switchSection = (nextSection: AdminSection) => {
    if (nextSection === selectedSection) return;
    setSelectedSection(nextSection);
    const url = new URL(window.location.href);
    if (nextSection === "dashboard") url.searchParams.delete("section");
    else url.searchParams.set("section", nextSection);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setSection(nextSection);
      return;
    }

    const token = ++transitionToken.current;
    setContentPhase("is-leaving");
    transitionTimer.current = window.setTimeout(() => {
      if (token !== transitionToken.current) return;
      setSection(nextSection);
      setContentPhase("is-entering");
      transitionFrame.current = requestAnimationFrame(() => setContentPhase(""));
    }, 125);
  };

  if (!loading && !user) {
    return (
      <Shell>
        <main className="shell admin-layout">
          <section className="admin-content">
            <div className="empty-state">{t("adm.signInRequired")} <a className="sender" href="/login">{t("adm.signIn")}</a></div>
          </section>
        </main>
      </Shell>
    );
  }
  if (!loading && user && user.role !== "admin") {
    return (
      <Shell>
        <main className="shell admin-layout">
          <section className="admin-content">
            <div className="empty-state">{t("adm.forbidden")}</div>
          </section>
        </main>
      </Shell>
    );
  }

  return (
    <Shell>
      <main className="shell admin-layout">
        <aside className="settings-sidebar">
          <h1>{t("adm.admin")}</h1>
          <nav className="settings-nav" aria-label={t("adm.sections")} ref={adminNavRef}>
            {sectionKeys.map((item) => (
              <button data-admin-section={item.id} className={selectedSection === item.id ? "active" : ""} key={item.id} type="button" aria-current={selectedSection === item.id ? "page" : undefined} onClick={() => switchSection(item.id)}>
                <span>{t(item.labelKey)}</span>
              </button>
            ))}
            <span className={`settings-nav-indicator ${navIndicator.ready ? "ready" : ""}`} style={{ width: navIndicator.width, height: navIndicator.height, transform: `translate(${navIndicator.x}px, ${navIndicator.y}px)` }} aria-hidden="true" />
            <span className={`settings-nav-accent ${navIndicator.ready ? "ready" : ""}`} style={{ transform: `translate(${navIndicator.x}px, ${navIndicator.y + 10}px)` }} aria-hidden="true" />
          </nav>
        </aside>

        <section className={`settings-content admin-content ${contentPhase}`} aria-live="polite">
          {section === "dashboard" && <DashboardSection />}
          {section === "users" && <UsersSection onNotify={onNotify} />}
          {section === "boards" && <BoardsSection onNotify={onNotify} />}
          {section === "moderation" && <ModerationSection onNotify={onNotify} />}
          {section === "audit" && <AuditSection />}
          {section === "feedback" && <FeedbackSection onNotify={onNotify} />}
        </section>
      </main>
    </Shell>
  );
}

// ---------------------------------------------------------------- dashboard

function StatGrid({ title, stats }: { title: string; stats: [string, number][] }) {
  return (
    <div className="admin-stat-group">
      <p className="admin-stat-group-title">{title}</p>
      <div className="admin-stat-grid">
        {stats.map(([label, value]) => (
          <div className="admin-stat" key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function DashboardSection() {
  const { t } = useI18n();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api.admin.stats();
      if (aliveRef.current) setStats(data);
    } catch (err) {
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : t("adm.loadStatsFail"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>{t("adm.retry")}</button>
      </div>
    );
  }
  if (!stats) return <Loading />;

  return (
    <div className="content-fade">
      <header><h2>{t("adm.dashboard")}</h2><p>{t("adm.dashDesc")}</p></header>
      <StatGrid title={t("adm.gUsers")} stats={[[t("adm.total"), stats.users.total], [t("adm.pending"), stats.users.pending], [t("adm.active"), stats.users.active], [t("adm.banned"), stats.users.banned], [t("adm.deactivated"), stats.users.deactivated]]} />
      <StatGrid title={t("adm.gContent")} stats={[[t("adm.discussions"), stats.content.discussions], [t("adm.replies"), stats.content.replies], [t("adm.boardsCount"), stats.content.boards]]} />
      <StatGrid title={t("adm.gModeration")} stats={[[t("adm.openReports"), stats.moderation.openReports], [t("adm.activeBans"), stats.moderation.activeBans]]} />
      <StatGrid title={t("adm.gActivity")} stats={[[t("adm.activeAuthors"), stats.activity.activeToday], [t("adm.newUsers"), stats.activity.newUsersToday], [t("adm.newDiscussions"), stats.activity.newDiscussionsToday], [t("adm.newReplies"), stats.activity.newRepliesToday], [t("adm.onlineNow"), stats.activity.onlineNow]]} />
      <p className="community-note">{t("adm.dashNote")}</p>
    </div>
  );
}

// ---------------------------------------------------------------- users

function UsersSection({ onNotify }: { onNotify: NotifyFn }) {
  const { user: me } = useAuth();
  const { locale, t } = useI18n();
  const [items, setItems] = useState<AdminUser[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [status, setStatus] = useState<UserStatus | "all">("all");
  const [role, setRole] = useState<UserRole | "all">("all");
  const [temporaryPassword, setTemporaryPassword] = useState<string | null>(null);

  // 只显示一次，不做 Esc/遮罩关闭，避免误丢密码；仅锁定背景滚动
  useModalScrollLock(temporaryPassword !== null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const loadFirst = useCallback(async () => {
    try {
      const data = await api.admin.users({
        q: debouncedQuery || undefined,
        status: status === "all" ? undefined : status,
        role: role === "all" ? undefined : role,
        limit: 20,
      });
      setItems(data.items);
      setNextCursor(data.nextCursor ? Number(data.nextCursor) : null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("adm.loadUsersFail"));
    } finally {
      setLoading(false);
    }
  }, [debouncedQuery, status, role]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api.admin
      .users({ q: debouncedQuery || undefined, status: status === "all" ? undefined : status, role: role === "all" ? undefined : role, limit: 20 })
      .then((data) => {
        if (!alive) return;
        setItems(data.items);
        setNextCursor(data.nextCursor ? Number(data.nextCursor) : null);
      })
      .catch((err) => { if (alive) setError(err instanceof ApiError ? err.message : t("adm.loadUsersFail")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [debouncedQuery, status, role]);

  const runAction = async (user: AdminUser, fn: () => Promise<unknown>, success: string) => {
    setBusyId(user.id);
    try {
      await fn();
      await loadFirst();
      onNotify(success, "success");
    } catch (err) {
      onNotify(t("adm.actionFail"), "error");
    } finally {
      setBusyId(null);
    }
  };

  const changeUserStatus = (user: AdminUser, next: UserStatus) => {
    if (next === user.status) return;
    if (next === "banned") {
      onNotify(t("adm.banManaged"), "error");
      return;
    }
    if (next === "pending") {
      onNotify(t("adm.pendingManaged"), "error");
      return;
    }
    const action = next === "active" && user.status === "banned"
      ? () => api.moderation.unban(user.username)
      : next === "active" && user.status === "pending"
        ? () => api.admin.verifyUser(user.id)
        : () => api.admin.changeStatus(user.id, { status: next });
    void runAction(user, action, t("adm.statusChanged", { status: t(userStatusKeys[next]) }));
  };

  return (
    <>
      <header><h2>{t("adm.users")}</h2><p>{t("adm.usersDesc")}</p></header>

      <div className="admin-filters">
        <label className="search-field admin-search">
          <SearchIcon />
          <span className="sr-only">{t("adm.searchUsers")}</span>
          <input type="search" placeholder={t("adm.searchUsersPh")} autoComplete="off" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <div className="admin-pills" role="group" aria-label={t("adm.filterStatus")}>
          {statusKeys.map((pill) => (
            <button className={`admin-pill ${status === pill.key ? "active" : ""}`} type="button" key={pill.key} onClick={() => setStatus(pill.key)}>{t(pill.labelKey)}</button>
          ))}
        </div>
        <SDropdown
          items={roleKeys}
          value={roleKeys.find((option) => option.key === role) ?? null}
          onChange={(option) => setRole(option.key)}
          getKey={(option) => option.key}
          getLabel={(option) => t(option.labelKey)}
          ariaLabel={t("adm.filterRole")}
          className="admin-dropdown"
        />
      </div>

      {error && <div className="empty-state">{error}</div>}
      {loading ? (
        <Loading />
      ) : (
        <div className="admin-list content-fade">
          {items.map((user) => (
            <div className="admin-row" key={user.id}>
              <div className="admin-row-main">
                <strong>{user.displayName}</strong>
                <span className="admin-muted">@{user.handle} · {user.email}</span>
                <div className="admin-row-tags">
                  <Badge variant={user.role}>{user.role === "admin" ? t("adm.admin") : t("adm.student")}</Badge>
                  <Badge variant={user.status}>{t(userStatusKeys[user.status])}</Badge>
                  {user.banActive && <Badge variant="banned">{t("adm.banActive")}</Badge>}
                  {!user.emailVerified && <Badge variant="pending">{t("adm.unverified")}</Badge>}
                  <span className="admin-muted">{t("adm.joined", { time: timeAgo(user.createdAt, locale) })}</span>
                </div>
              </div>
              <div className="admin-row-actions" data-busy={busyId === user.id || undefined} aria-label={t("adm.actionsFor", { name: user.displayName })}>
                <SDropdown
                  items={["student", "admin"] as UserRole[]}
                  value={user.role}
                  onChange={(nextRole) => void runAction(user, () => api.admin.changeRole(user.id, { role: nextRole }), t("adm.roleUpdated"))}
                  getKey={(item) => item}
                  getLabel={(item) => (item === "admin" ? t("adm.admin") : t("adm.student"))}
                  label={t("adm.role")}
                  ariaLabel={t("adm.roleFor", { name: user.displayName })}
                  className="admin-control admin-dropdown"
                  disabled={busyId !== null || user.id === me?.id}
                />
                <SDropdown
                  items={["pending", "active", "banned", "deactivated"] as UserStatus[]}
                  value={user.status}
                  onChange={(nextStatus) => changeUserStatus(user, nextStatus)}
                  getKey={(item) => item}
                  getLabel={(item) => t(userStatusKeys[item])}
                  label={t("adm.status")}
                  ariaLabel={t("adm.statusFor", { name: user.displayName })}
                  className="admin-control admin-dropdown"
                  disabled={busyId !== null || user.id === me?.id}
                />
                {user.id !== me?.id && user.status !== "banned" && (
                  <button className="admin-btn" type="button" disabled={busyId !== null} onClick={() => void (async () => {
                    setBusyId(user.id);
                    try {
                      const result = await api.admin.resetPassword(user.id);
                      setTemporaryPassword(result.temporaryPassword);
                      await loadFirst();
                    } catch (err) {
                      onNotify(t("adm.actionFail"), "error");
                    } finally {
                      setBusyId(null);
                    }
                  })()}>{t("adm.resetPw")}</button>
                )}
                {user.id !== me?.id && (
                  <AlertDialog.Root>
                    <AlertDialog.Trigger asChild>
                      <button className="admin-btn danger" type="button" disabled={busyId !== null}>{t("adm.deleteUser")}</button>
                    </AlertDialog.Trigger>
                    <AlertDialog.Portal>
                      <AlertDialog.Overlay className="dialog-overlay" />
                      <AlertDialog.Content className="dialog-content">
                        <AlertDialog.Title className="dialog-title">{t("adm.deleteUserTitle", { name: user.displayName })}</AlertDialog.Title>
                        <AlertDialog.Description className="dialog-description">{t("adm.deleteUserDesc")}</AlertDialog.Description>
                        <div className="dialog-actions">
                          <AlertDialog.Cancel asChild><button type="button" className="action-btn">{t("adm.cancel")}</button></AlertDialog.Cancel>
                          <AlertDialog.Action asChild><button type="button" className="dialog-danger" onClick={() => void runAction(user, () => api.admin.deleteUser(user.id), t("adm.userDeleted"))}>{t("adm.deleteUser")}</button></AlertDialog.Action>
                        </div>
                      </AlertDialog.Content>
                    </AlertDialog.Portal>
                  </AlertDialog.Root>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {!loading && nextCursor !== null && (
        <button className="admin-btn load-more" type="button" onClick={() => void (async () => {
          try {
            const data = await api.admin.users({ q: debouncedQuery || undefined, status: status === "all" ? undefined : status, role: role === "all" ? undefined : role, cursor: nextCursor, limit: 20 });
            setItems((prev) => [...prev, ...data.items]);
            setNextCursor(data.nextCursor ? Number(data.nextCursor) : null);
          } catch (err) {
            onNotify(t("adm.loadMoreFail"), "error");
          }
        })()}>{t("adm.loadMore")}</button>
      )}
      {!loading && items.length === 0 && <div className="empty-state">{t("adm.noUsers")}</div>}
      {temporaryPassword && (
        <div className="dialog-overlay">
          <div className="dialog-content feedback-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <h2 className="dialog-title">{t("adm.tempPwTitle")}</h2>
            <p className="admin-muted">{t("adm.copyOnce")}</p>
            <label className="form-field"><span>{t("adm.tempPw")}</span><input readOnly value={temporaryPassword} onFocus={(event) => event.target.select()} /></label>
            <div className="dialog-actions">
              <button className="primary-action" type="button" onClick={() => void (async () => {
                try {
                  await navigator.clipboard.writeText(temporaryPassword);
                  onNotify(t("adm.copied"));
                } catch {
                  onNotify(t("adm.copyFail"), "error");
                }
              })()}>{t("adm.copy")}</button>
              <button className="action-btn" type="button" onClick={() => setTemporaryPassword(null)}>{t("adm.close")}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- boards

const VISIBILITIES: BoardVisibility[] = ["public", "members", "private"];
const POSTING_POLICIES: ("everyone" | "members" | "moderators")[] = ["everyone", "members", "moderators"];

function BoardsSection({ onNotify }: { onNotify: NotifyFn }) {
  const { t } = useI18n();
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingSlug, setEditingSlug] = useState<string | null>(null);
  const [membersSlug, setMembersSlug] = useState<string | null>(null);
  const [membersMap, setMembersMap] = useState<Record<string, { id: number; username: string; handle: string; displayName: string; role: "member" | "moderator" }[]>>({});

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSlug, setCreateSlug] = useState("");
  const [createDesc, setCreateDesc] = useState("");
  const [createVisibility, setCreateVisibility] = useState<BoardVisibility>("public");
  const [createPosting, setCreatePosting] = useState<"everyone" | "members" | "moderators">("everyone");
  const [createBusy, setCreateBusy] = useState(false);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await api.boards.list();
      if (aliveRef.current) setBoards(data.items);
    } catch (err) {
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : t("adm.loadBoardsFail"));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const flash = (message: string, tone?: "success" | "error") => {
    onNotify(message, tone);
  };

  const createBoard = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (createBusy) return;
    setCreateBusy(true);
    try {
      await api.boards.create({ name: createName.trim(), slug: createSlug.trim(), description: createDesc.trim(), visibility: createVisibility, postingPolicy: createPosting });
      setCreateOpen(false);
      setCreateName("");
      setCreateSlug("");
      setCreateDesc("");
      setCreateVisibility("public");
      setCreatePosting("everyone");
      flash(t("adm.boardCreated"), "success");
      await load();
    } catch (err) {
      flash(err instanceof ApiError ? err.message : t("adm.createBoardFail"));
    } finally {
      setCreateBusy(false);
    }
  };

  const deleteBoard = async (board: BoardSummary) => {
    try {
      await api.boards.del(board.slug, { reason: "admin delete" });
      flash(t("adm.boardDeleted"), "success");
      await load();
    } catch (err) {
      flash(err instanceof ApiError ? err.message : t("adm.deleteBoardFail"));
    }
  };

  const toggleMembers = async (slug: string) => {
    if (membersSlug === slug) {
      setMembersSlug(null);
      return;
    }
    setMembersSlug(slug);
    try {
      const data = await api.boards.members(slug);
      setMembersMap((prev) => ({ ...prev, [slug]: data.items }));
    } catch (err) {
      flash(err instanceof ApiError ? err.message : t("adm.loadMembersFail"));
    }
  };

  return (
    <>
      <header><h2>{t("adm.boards")}</h2><p>{t("adm.boardsDesc")}</p></header>

      {error && <div className="empty-state">{error}</div>}

      <div className="admin-create-toggle">
        <button className="admin-btn" type="button" onClick={() => setCreateOpen((v) => !v)}>{createOpen ? t("adm.cancel") : t("adm.createBoard")}</button>
      </div>
      {createOpen && (
        <form className="admin-inline-form" onSubmit={createBoard} noValidate>
          <label><span>{t("adm.name")}</span><input value={createName} onChange={(e) => setCreateName(e.target.value)} maxLength={60} required /></label>
          <label><span>{t("adm.slug")}</span><input value={createSlug} onChange={(e) => setCreateSlug(e.target.value)} pattern="[a-z0-9-]+" maxLength={50} required placeholder={t("adm.slugPlaceholder")} /></label>
          <label><span>{t("adm.description")}</span><input value={createDesc} onChange={(e) => setCreateDesc(e.target.value)} maxLength={500} /></label>
          <div className="admin-inline-selects">
            <SDropdown
              items={VISIBILITIES}
              value={createVisibility}
              onChange={(value) => setCreateVisibility(value)}
              getKey={(item) => item}
              getLabel={(item) => t(visKeys[item])}
              label={t("adm.visibility")}
              ariaLabel={t("adm.boardVisibility")}
              className="admin-dropdown"
            />
            <SDropdown
              items={POSTING_POLICIES}
              value={createPosting}
              onChange={(value) => setCreatePosting(value)}
              getKey={(item) => item}
              getLabel={(item) => t(postingKeys[item])}
              label={t("adm.posting")}
              ariaLabel={t("adm.boardPosting")}
              className="admin-dropdown"
            />
          </div>
          <button className="primary-action" type="submit" disabled={createBusy || !createName.trim() || !createSlug.trim()}>{createBusy ? t("adm.creating") : t("adm.createBoard")}</button>
        </form>
      )}

      {loading ? (
        <Loading />
      ) : (
        <div className="admin-list content-fade">
          {boards.map((board) => (
            <div className="admin-row admin-row-stacked" key={board.slug}>
              <div className="admin-row-main">
                <strong>{board.name}</strong>
                <span className="admin-muted">{board.description || board.slug}</span>
                <div className="admin-row-tags">
                  <Badge variant={board.visibility}>{t(visKeys[board.visibility])}</Badge>
                  <Badge variant="active">{t(postingKeys[board.postingPolicy])}</Badge>
                  <span className="admin-muted">{t("adm.membersCount", { count: board.memberCount, today: board.todayActivity })}</span>
                </div>
              </div>
              <div className="admin-row-actions">
                <button className="admin-btn" type="button" onClick={() => { setEditingSlug(editingSlug === board.slug ? null : board.slug); setMembersSlug(null); }}>{editingSlug === board.slug ? t("adm.done") : t("adm.edit")}</button>
                <button className="admin-btn" type="button" onClick={() => void toggleMembers(board.slug)}>{membersSlug === board.slug ? t("adm.hideMembers") : t("adm.members")}</button>
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn danger" type="button">{t("adm.delete")}</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">{t("adm.deleteBoardTitle", { name: board.name })}</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">
                        {t("adm.deleteBoardDesc")}
                      </AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">{t("adm.cancel")}</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void deleteBoard(board)}>{t("adm.delete")}</button>
                        </AlertDialog.Action>
                      </div>
                    </AlertDialog.Content>
                  </AlertDialog.Portal>
                </AlertDialog.Root>
              </div>

              {editingSlug === board.slug && (
                <BoardEditForm board={board} onDone={() => { setEditingSlug(null); void load(); }} onError={flash} />
              )}
              {membersSlug === board.slug && (
                <div className="admin-members">
                  {(membersMap[board.slug] ?? []).map((member) => (
                    <div className="admin-member" key={member.id}>
                      <span className="admin-muted">@{member.handle}</span>
                        <SDropdown
                          items={["member", "moderator"] as ("member" | "moderator")[]}
                          value={member.role}
                          onChange={(nextRole) => void (async () => {
                            try {
                              await api.boards.updateMemberRole(board.slug, member.id, { role: nextRole });
                              setMembersMap((prev) => ({ ...prev, [board.slug]: (prev[board.slug] ?? []).map((m) => (m.id === member.id ? { ...m, role: nextRole } : m)) }));
                              flash(t("adm.roleUpdatedMember"), "success");
                            } catch (err) {
                              flash(err instanceof ApiError ? err.message : t("adm.roleUpdateFail"));
                            }
                          })()}
                          getKey={(item) => item}
                          getLabel={(item) => (item === "moderator" ? t("adm.moderator") : t("adm.member"))}
                          ariaLabel={t("adm.roleForMember", { handle: member.handle })}
                          className="member-role admin-dropdown"
                        />
                      </div>
                    ))}
                    {membersMap[board.slug]?.length === 0 && <p className="admin-muted">{t("adm.noMembers")}</p>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {!loading && boards.length === 0 && <div className="empty-state">{t("adm.noBoards")}</div>}
    </>
  );
}

function BoardEditForm({ board, onDone, onError }: { board: BoardSummary; onDone: () => void; onError: (message: string) => void }) {
  const { t } = useI18n();
  const [name, setName] = useState(board.name);
  const [desc, setDesc] = useState(board.description);
  const [visibility, setVisibility] = useState(board.visibility);
  const [posting, setPosting] = useState(board.postingPolicy);
  const [busy, setBusy] = useState(false);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.boards.update(board.slug, { name: name.trim(), description: desc.trim(), visibility, postingPolicy: posting });
      onDone();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : t("adm.saveBoardFail"));
      setBusy(false);
    }
  };

  return (
    <form className="admin-inline-form" onSubmit={save} noValidate>
      <label><span>{t("adm.name")}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required /></label>
      <label><span>{t("adm.description")}</span><input value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} /></label>
      <div className="admin-inline-selects">
        <SDropdown items={VISIBILITIES} value={visibility} onChange={(value) => setVisibility(value)} getKey={(item) => item} getLabel={(item) => t(visKeys[item])} label={t("adm.visibility")} ariaLabel={t("adm.boardVisibility")} className="admin-dropdown" />
        <SDropdown items={POSTING_POLICIES} value={posting} onChange={(value) => setPosting(value)} getKey={(item) => item} getLabel={(item) => t(postingKeys[item])} label={t("adm.posting")} ariaLabel={t("adm.boardPosting")} className="admin-dropdown" />
      </div>
      <button className="primary-action" type="submit" disabled={busy || !name.trim()}>{busy ? t("adm.saving") : t("adm.saveBoard")}</button>
    </form>
  );
}

// ---------------------------------------------------------------- moderation

function ModerationSection({ onNotify }: { onNotify: NotifyFn }) {
  const { t } = useI18n();
  const [tab, setTab] = useState<"reports" | "deleted">("reports");
  return (
    <>
      <header className="admin-section-header"><h2>{t("adm.moderation")}</h2></header>
      <div className="admin-pills admin-section-tabs" role="tablist" aria-label={t("adm.modViews")}>
        <button className={`admin-pill ${tab === "reports" ? "active" : ""}`} type="button" role="tab" aria-selected={tab === "reports"} onClick={() => setTab("reports")}>{t("adm.openReportsTab")}</button>
        <button className={`admin-pill ${tab === "deleted" ? "active" : ""}`} type="button" role="tab" aria-selected={tab === "deleted"} onClick={() => setTab("deleted")}>{t("adm.deletedTab")}</button>
      </div>
      {tab === "reports" ? <ReportsList onNotify={onNotify} /> : <DeletedList onNotify={onNotify} />}
    </>
  );
}

function ReportsList({ onNotify }: { onNotify: NotifyFn }) {
  const { locale, t } = useI18n();
  const [items, setItems] = useState<ReportDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await api.moderation.reports({ status: "open", limit: 30 });
      if (aliveRef.current) setItems(data.items);
    } catch (err) {
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : t("adm.loadReportsFail"));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (report: ReportDTO, fn: () => Promise<unknown>, success: string) => {
    setBusyId(report.id);
    try {
      await fn();
      await load();
      onNotify(success, "success");
    } catch (err) {
      onNotify(t("adm.actionFail"), "error");
    } finally {
      setBusyId(null);
    }
  };

  const targetHref = (report: ReportDTO) => {
    const t = report.target;
    if (!t) return null;
    if (t.type === "discussion") return `/d/${t.id}`;
    if (t.type === "reply" && t.discussionId) return `/d/${t.discussionId}`;
    if (t.type === "user") return t.username ? `/profile?username=${encodeURIComponent(t.username)}` : null;
    return null;
  };

  return (
    <>
      {error && <div className="empty-state">{error}</div>}
      {loading ? (
        <Loading />
      ) : (
        <div className="admin-list content-fade">
          {items.map((report) => {
            const href = targetHref(report);
            const target = report.target;
            const banUsername = target?.type === "user" ? target.username ?? null : null;
            return (
              <div className="admin-row" key={report.id}>
                <div className="admin-row-main">
                  <strong>{target?.title ?? target?.displayName ?? target?.handle ?? target?.username ?? `#${report.reportableId}`}</strong>
                  {href && <a className="sender" href={href}>{t("adm.view")}</a>}
                  <span className="admin-muted">{report.reason || t("adm.noReason")} · {t("adm.reportedBy", { handle: report.reporter.handle })} · {timeAgo(report.createdAt, locale)}</span>
                </div>
                <div className="admin-row-actions">
                  <button className="admin-btn" type="button" disabled={busyId !== null} onClick={() => void run(report, () => api.moderation.resolveReport(report.id, { status: "in_progress", action: "report.in_progress" }), t("adm.markedProgress"))}>{t("adm.inProgress")}</button>
                  <button className="admin-btn" type="button" disabled={busyId !== null} onClick={() => void run(report, () => api.moderation.resolveReport(report.id, { status: "resolved", action: "report.resolved" }), t("adm.reportResolved"))}>{t("adm.resolve")}</button>
                  <button className="admin-btn" type="button" disabled={busyId !== null} onClick={() => void run(report, () => api.moderation.resolveReport(report.id, { status: "dismissed", action: "report.dismissed" }), t("adm.reportDismissed"))}>{t("adm.dismiss")}</button>
                  {target?.type === "user" && (
                    <button className="admin-btn danger" type="button" disabled={busyId !== null || !banUsername} onClick={() => { if (banUsername) void run(report, () => api.moderation.ban({ username: banUsername }), t("adm.userBanned")); }}>{t("adm.ban")}</button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {!loading && items.length === 0 && <div className="empty-state">{t("adm.noOpenReports")}</div>}
    </>
  );
}

function DeletedList({ onNotify }: { onNotify: NotifyFn }) {
  const { locale, t } = useI18n();
  const [discussions, setDiscussions] = useState<DeletedDiscussion[]>([]);
  const [replies, setReplies] = useState<DeletedReply[]>([]);
  const [nextDiscCursor, setNextDiscCursor] = useState<number | null>(null);
  const [nextReplyCursor, setNextReplyCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await api.admin.deletedContent({ limit: 10 });
      if (!aliveRef.current) return;
      setDiscussions(data.discussions);
      setReplies(data.replies);
      setNextDiscCursor(data.nextDiscussionCursor);
      setNextReplyCursor(data.nextReplyCursor);
    } catch (err) {
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : t("adm.loadDeletedFail"));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const restore = async (targetType: "discussion" | "reply", targetId: number) => {
    setBusyKey(`${targetType}:${targetId}`);
    try {
      await api.moderation.restore({ targetType, targetId });
      await load();
      onNotify(t("adm.restored"), "success");
    } catch (err) {
      onNotify(t("adm.restoreFail"), "error");
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <>
      {error && <div className="empty-state">{error}</div>}
      {loading ? (
        <Loading />
      ) : (
        <div className="content-fade">
          <p className="admin-group-label">{t("adm.deletedDiscussions")}</p>
          <div className="admin-list">
            {discussions.map((d) => (
              <div className="admin-row" key={d.id}>
                <div className="admin-row-main">
                  <strong>{d.title}</strong>
                  <span className="admin-muted">{d.preview} · /{d.boardSlug}</span>
                  <div className="admin-row-tags">
                    {d.deletedBy && <span className="admin-muted">by @{d.deletedBy.handle}</span>}
                    <span className="admin-muted">{timeAgo(d.deletedAt, locale)}</span>
                  </div>
                </div>
                <div className="admin-row-actions">
                  <button className="admin-btn" type="button" disabled={busyKey !== null} onClick={() => void restore("discussion", d.id)}>{t("adm.restore")}</button>
                </div>
              </div>
            ))}
          </div>
          {nextDiscCursor !== null && (
            <button className="admin-btn load-more" type="button" onClick={() => void (async () => {
              try {
                const data = await api.admin.deletedContent({ discussionCursor: nextDiscCursor, limit: 10 });
                setDiscussions((prev) => [...prev, ...data.discussions]);
                setNextDiscCursor(data.nextDiscussionCursor);
              } catch (err) {
                onNotify(t("adm.loadMoreFail"), "error");
              }
            })()}>{t("adm.loadMoreDiscussions")}</button>
          )}

          <p className="admin-group-label">{t("adm.deletedReplies")}</p>
          <div className="admin-list">
            {replies.map((r) => (
              <div className="admin-row" key={r.id}>
                <div className="admin-row-main">
                  <strong>{r.discussionTitle || t("adm.replyFallback")}</strong>
                  <span className="admin-muted">{r.preview}</span>
                  <div className="admin-row-tags">
                    <a className="sender" href={`/d/${r.discussionId}`}>{t("adm.viewThread")}</a>
                    <span className="admin-muted">{timeAgo(r.deletedAt, locale)}</span>
                  </div>
                </div>
                <div className="admin-row-actions">
                  <button className="admin-btn" type="button" disabled={busyKey !== null} onClick={() => void restore("reply", r.id)}>{t("adm.restore")}</button>
                </div>
              </div>
            ))}
          </div>
          {nextReplyCursor !== null && (
            <button className="admin-btn load-more" type="button" onClick={() => void (async () => {
              try {
                const data = await api.admin.deletedContent({ replyCursor: nextReplyCursor, limit: 10 });
                setReplies((prev) => [...prev, ...data.replies]);
                setNextReplyCursor(data.nextReplyCursor);
              } catch (err) {
                onNotify(t("adm.loadMoreFail"), "error");
              }
            })()}>{t("adm.loadMoreReplies")}</button>
          )}
          {discussions.length === 0 && replies.length === 0 && <div className="empty-state">{t("adm.noDeleted")}</div>}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- audit

function AuditSection() {
  const { locale, t } = useI18n();
  const [items, setItems] = useState<ModerationAction[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.moderation
      .actions({ limit: 30 })
      .then((data) => { if (!alive) return; setItems(data.items); setNextCursor(data.nextCursor ? Number(data.nextCursor) : null); })
      .catch((err) => { if (alive) setError(err instanceof ApiError ? err.message : t("adm.loadAuditFail")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  return (
    <>
      <header><h2>{t("adm.audit")}</h2><p>{t("adm.auditDesc")}</p></header>
      {error && <div className="empty-state">{error}</div>}
      {loading ? (
        <Loading />
      ) : (
        <div className="admin-list content-fade">
          {items.map((action) => (
            <div className="admin-row" key={action.id}>
              <div className="admin-row-main">
                <strong>{action.action}</strong>
                <span className="admin-muted">{action.actor.displayName} (@{action.actor.handle}) → {action.targetType}#{action.targetId}</span>
                {action.reason && <span className="admin-muted">· {action.reason}</span>}
              </div>
              <div className="admin-row-tags"><span className="admin-muted">{timeAgo(action.createdAt, locale)}</span></div>
            </div>
          ))}
        </div>
      )}
      {!loading && nextCursor !== null && (
        <button className="admin-btn load-more" type="button" onClick={() => void (async () => {
          try {
            const data = await api.moderation.actions({ cursor: nextCursor, limit: 30 });
            setItems((prev) => [...prev, ...data.items]);
            setNextCursor(data.nextCursor ? Number(data.nextCursor) : null);
          } catch (err) {
            setError(err instanceof ApiError ? err.message : t("adm.loadMoreFail"));
          }
        })()}>{t("adm.loadMore")}</button>
      )}
      {!loading && items.length === 0 && <div className="empty-state">{t("adm.noActions")}</div>}
    </>
  );
}

// ---------------------------------------------------------------- shell

function Shell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <>
      <header className="topbar">
        <div className="shell topbar-inner">
          <a href="/" className="wordmark" aria-label={t("nav.home")}>Samryetha</a>
          <nav className="primary-nav" aria-label={t("nav.primary")}>
            <a className="nav-link" href="/" data-view="latest">{t("nav.latest")}</a>
            <a className="nav-link" href="/" data-view="followed">{t("nav.followed")}</a>
            <a className="nav-link" href="/" data-view="boards">{t("nav.boards")}</a>
          </nav>
          <div className="actions">
            <label className="search-field">
              <SearchIcon />
              <span className="sr-only">{t("nav.searchDiscussions")}</span>
              <input type="search" placeholder={t("nav.searchDiscussions")} autoComplete="off" />
            </label>
            <MobileMenu />
            <UserMenu current="admin" />
            <a className="compose" href="/post">{t("nav.post")}</a>
          </div>
        </div>
      </header>
      {children}
    </>
  );
}

// ---------------------------------------------------------------- feedback

type FeedbackTab = "projects" | "keys" | "backup";
type MemberFlags = Record<number, { member: boolean; programmer: boolean }>;

const BACKUP_CRONS = ["", "0 * * * *", "0 3 * * *", "0 3 * * 1", "0 3 1 * *"];

function FeedbackSection({ onNotify }: { onNotify: NotifyFn }) {
  const { t } = useI18n();
  const [tab, setTab] = useState<FeedbackTab>("projects");
  const tabs = [
    ["projects", t("adm.fbProjects")],
    ["keys", t("adm.fbKeys")],
    ["backup", t("adm.fbBackup")],
  ] as [FeedbackTab, string][];
  return (
    <div className="content-fade">
      <header className="admin-section-header"><h2>{t("adm.feedback")}</h2></header>
      <div className="admin-pills admin-section-tabs" role="tablist" aria-label={t("adm.fbViews")}>
        {tabs.map(([id, label]) => (
          <button key={id} className={`admin-pill ${tab === id ? "active" : ""}`} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      {tab === "projects" && <FeedbackProjectsView onNotify={onNotify} />}
      {tab === "keys" && <FeedbackKeysView onNotify={onNotify} />}
      {tab === "backup" && <FeedbackBackupView onNotify={onNotify} />}
    </div>
  );
}

function FeedbackProjectsView({ onNotify }: { onNotify: NotifyFn }) {
  const { t } = useI18n();
  const [projects, setProjects] = useState<FeedbackProjectAdmin[]>([]);
  const [userOptions, setUserOptions] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<FeedbackProjectAdmin | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "" });
  const [flags, setFlags] = useState<MemberFlags>({});
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<FeedbackProjectAdmin | null>(null);
  const [saving, setSaving] = useState(false);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const [p, u] = await Promise.all([
        api.feedbackAdmin.projects(),
        api.admin.users({ status: "active", limit: 50 }),
      ]);
      if (!aliveRef.current) return;
      setProjects(p.items);
      setUserOptions(u.items);
    } catch (err) {
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : t("adm.loadProjectsFail"));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useModalScrollLock(modalOpen);
  useEscapeKey(modalOpen, () => setModalOpen(false));

  const openCreate = () => {
    setEditing(null);
    setForm({ name: "", description: "" });
    setFlags({});
    setFormError("");
    setModalOpen(true);
  };

  const openEdit = (p: FeedbackProjectAdmin) => {
    setEditing(p);
    setForm({ name: p.name, description: p.description });
    const next: MemberFlags = {};
    for (const m of p.members) next[m.userId] = { member: true, programmer: m.isProgrammer };
    setFlags(next);
    setFormError("");
    setModalOpen(true);
  };

  const save = async () => {
    if (saving) return;
    if (!form.name.trim()) {
      setFormError(t("adm.projectNameRequired"));
      return;
    }
    const members = Object.entries(flags)
      .filter(([, v]) => v.member)
      .map(([userId, v]) => ({ userId: Number(userId), isProgrammer: v.programmer }));
    setSaving(true);
    try {
      if (editing) {
        await api.feedbackAdmin.updateProject(editing.id, { name: form.name.trim(), description: form.description });
        await api.feedbackAdmin.setMembers(editing.id, members);
      } else {
        const created = await api.feedbackAdmin.createProject({ name: form.name.trim(), description: form.description });
        await api.feedbackAdmin.setMembers(created.id, members);
      }
      setModalOpen(false);
      onNotify(t("adm.projectSaved"), "success");
      void load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("adm.saveProjectFail"));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (p: FeedbackProjectAdmin) => {
    try {
      await api.feedbackAdmin.delProject(p.id);
      setDeleting(null);
      onNotify(t("adm.projectDeleted"), "success");
      void load();
    } catch (err) {
      onNotify(t("adm.deleteProjectFail"), "error");
      setDeleting(null);
    }
  };

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>{t("adm.retry")}</button>
      </div>
    );
  }
  if (loading) return <Loading />;

  return (
    <>
      <div className="view-head" style={{ marginTop: 8 }}>
        <p className="admin-muted">{t("adm.projectsNote")}</p>
        <button className="admin-btn" type="button" onClick={openCreate}>{t("adm.newProject")}</button>
      </div>

      <div className="admin-list">
        {projects.length === 0 ? (
          <div className="empty-state">{t("adm.noProjects")}</div>
        ) : (
          projects.map((p) => (
            <div className="admin-row admin-row-stacked" key={p.id}>
              <div className="admin-row-main">
                <strong>{p.name}</strong>
                <span className="admin-muted">{p.description || "—"}</span>
                <div className="admin-row-tags">
                  <span className="admin-muted">
                    {p.members.length
                      ? p.members.map((m) => `${m.handle}${m.isProgrammer ? ` (${t("adm.programmer")})` : ""}`).join(", ")
                      : t("adm.noMembersShort")}
                  </span>
                </div>
              </div>
              <div className="admin-row-actions">
                <button className="admin-btn" type="button" onClick={() => openEdit(p)}>{t("adm.edit")}</button>
                <AlertDialog.Root open={deleting?.id === p.id} onOpenChange={(o) => !o && setDeleting(null)}>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn danger" type="button" onClick={() => setDeleting(p)}>{t("adm.delete")}</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">{t("adm.deleteProjectTitle", { name: p.name })}</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">
                        {t("adm.deleteProjectDesc")}
                      </AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">{t("adm.cancel")}</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void remove(p)}>{t("adm.delete")}</button>
                        </AlertDialog.Action>
                      </div>
                    </AlertDialog.Content>
                  </AlertDialog.Portal>
                </AlertDialog.Root>
              </div>
            </div>
          ))
        )}
      </div>

      {modalOpen && (
        <div className="dialog-overlay" onClick={() => setModalOpen(false)}>
          <div className="dialog-content feedback-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h2 className="dialog-title">{editing ? t("adm.editProject") : t("adm.newProjectTitle")}</h2>
            <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
              <label className="form-field">
                <span>{t("adm.name")}</span>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={64} autoFocus />
              </label>
              <label className="form-field">
                <span>{t("adm.description")}</span>
                <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={500} rows={3} />
              </label>
              <h4 className="sub-title">{t("adm.membersTitle")}</h4>
              <div className="member-picker">
                {userOptions.length === 0 ? (
                  <div className="admin-muted">{t("adm.noActiveUsers")}</div>
                ) : (
                  userOptions.map((u) => {
                    const flag = flags[u.id] ?? { member: false, programmer: false };
                    return (
                      <div className="member-row" key={u.id}>
                        <label><input type="checkbox" checked={flag.member} onChange={(e) => setFlags((prev) => ({ ...prev, [u.id]: { member: e.target.checked, programmer: flag.programmer } }))} /> @{u.handle}</label>
                        <label className="muted"><input type="checkbox" disabled={!flag.member} checked={flag.member && flag.programmer} onChange={(e) => setFlags((prev) => ({ ...prev, [u.id]: { member: true, programmer: e.target.checked } }))} /> {t("adm.programmer")}</label>
                      </div>
                    );
                  })
                )}
              </div>
              {formError && <div className="dialog-error">{formError}</div>}
              <div className="dialog-actions">
                <button type="button" className="action-btn" onClick={() => setModalOpen(false)}>{t("adm.cancel")}</button>
                <button type="submit" className="primary-action" disabled={saving}>{saving ? t("adm.saving") : t("adm.save")}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

function FeedbackKeysView({ onNotify }: { onNotify: NotifyFn }) {
  const { locale, t } = useI18n();
  const [keys, setKeys] = useState<FeedbackApiKey[]>([]);
  const [projects, setProjects] = useState<FeedbackProjectAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ name: "", role: "read" as "read" | "write" });
  const [scopedIds, setScopedIds] = useState<number[]>([]);
  const [formError, setFormError] = useState("");
  const [shownKey, setShownKey] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const [k, p] = await Promise.all([api.feedbackAdmin.keys(), api.feedbackAdmin.projects()]);
      if (!aliveRef.current) return;
      setKeys(k.items);
      setProjects(p.items);
    } catch (err) {
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : t("adm.loadKeysFail"));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useModalScrollLock(createOpen || shownKey !== null);
  useEscapeKey(createOpen, () => setCreateOpen(false));

  const create = async () => {
    if (creating) return;
    if (!form.name.trim()) {
      setFormError(t("adm.keyNameRequired"));
      return;
    }
    setCreating(true);
    try {
      const res = await api.feedbackAdmin.createKey({ name: form.name.trim(), role: form.role, projectIds: scopedIds });
      setShownKey(res.key);
      setCreateOpen(false);
      setForm({ name: "", role: "read" });
      setScopedIds([]);
      void load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("adm.createKeyFail"));
    } finally {
      setCreating(false);
    }
  };

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>{t("adm.retry")}</button>
      </div>
    );
  }
  if (loading) return <Loading />;

  return (
    <>
      <div className="view-head" style={{ marginTop: 8 }}>
        <p className="admin-muted">{t("adm.keysNote")}</p>
        <button className="admin-btn" type="button" onClick={() => { setForm({ name: "", role: "read" }); setScopedIds([]); setFormError(""); setCreateOpen(true); }}>{t("adm.newKey")}</button>
      </div>

      <div className="admin-list">
        {keys.length === 0 ? (
          <div className="empty-state">{t("adm.noKeys")}</div>
        ) : (
          keys.map((k) => (
            <div className="admin-row admin-row-stacked" key={k.id}>
              <div className="admin-row-main">
                <strong>{k.name} <span className="admin-badge bug">#{k.prefix}…</span> <Badge variant={k.role}>{k.role}</Badge> <Badge variant={k.enabled ? "done" : "expired"}>{t(k.enabled ? "adm.enabled" : "adm.disabled")}</Badge></strong>
                <span className="admin-muted">
                  {t("adm.scope", { scope: k.projectIds.length ? k.projectIds.map((id) => projects.find((p) => p.id === id)?.name ?? `#${id}`).join(", ") : t("adm.allProjects") })}
                  {k.lastUsedAt ? ` · ${t("adm.lastUsed", { time: timeAgo(k.lastUsedAt, locale) })}` : ""}
                </span>
              </div>
              <div className="admin-row-actions">
                <button className="admin-btn" type="button" onClick={() => void (async () => {
                  try {
                    await api.feedbackAdmin.setKeyEnabled(k.id, !k.enabled);
                    void load();
                  } catch (err) {
                    onNotify(t("adm.toggleKeyFail"), "error");
                  }
                })()}>{t(k.enabled ? "adm.disable" : "adm.enable")}</button>
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn danger" type="button">{t("adm.delete")}</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">{t("adm.deleteKeyTitle", { name: k.name })}</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">{t("adm.keyStops")}</AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">{t("adm.cancel")}</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void (async () => {
                            try {
                              await api.feedbackAdmin.delKey(k.id);
                              void load();
                            } catch (err) {
                              onNotify(t("adm.deleteKeyFail"), "error");
                            }
                          })()}>{t("adm.delete")}</button>
                        </AlertDialog.Action>
                      </div>
                    </AlertDialog.Content>
                  </AlertDialog.Portal>
                </AlertDialog.Root>
              </div>
            </div>
          ))
        )}
      </div>

      {createOpen && (
        <div className="dialog-overlay" onClick={() => setCreateOpen(false)}>
          <div className="dialog-content feedback-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h2 className="dialog-title">{t("adm.newAgentKey")}</h2>
            <form onSubmit={(e) => { e.preventDefault(); void create(); }}>
              <label className="form-field">
                <span>{t("adm.name")}</span>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={64} placeholder={t("adm.keyNamePh")} autoFocus />
              </label>
              <SDropdown
                items={["read", "write"] as ("read" | "write")[]}
                value={form.role}
                onChange={(role) => setForm({ ...form, role })}
                getKey={(item) => item}
                getLabel={(item) => (item === "read" ? t("adm.readOnly") : t("adm.readWrite"))}
                label={t("adm.role")}
                ariaLabel={t("adm.keyRoleAria")}
                className="form-dropdown"
              />
              <h4 className="sub-title">{t("adm.keyProjects")}</h4>
              <div className="member-picker">
                {projects.length === 0 ? (
                  <div className="admin-muted">{t("adm.noProjectsShort")}</div>
                ) : (
                  projects.map((p) => (
                    <div className="member-row" key={p.id}>
                      <label>
                        <input
                          type="checkbox"
                          checked={scopedIds.includes(p.id)}
                          onChange={(e) => setScopedIds((prev) => (e.target.checked ? [...prev, p.id] : prev.filter((x) => x !== p.id)))}
                        />
                        {p.name}
                      </label>
                    </div>
                  ))
                )}
              </div>
              {formError && <div className="dialog-error">{formError}</div>}
              <div className="dialog-actions">
                <button type="button" className="action-btn" onClick={() => setCreateOpen(false)}>{t("adm.cancel")}</button>
                <button type="submit" className="primary-action" disabled={creating}>{creating ? t("adm.creating") : t("adm.create")}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {shownKey && (
        <div className="dialog-overlay" onClick={() => setShownKey(null)}>
          <div className="dialog-content feedback-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h2 className="dialog-title">{t("adm.keyCreated")}</h2>
            <p className="admin-muted">{t("adm.keyHint")}</p>
            <label className="form-field">
              <span>{t("adm.apiKey")}</span>
              <textarea readOnly value={shownKey} rows={2} onFocus={(e) => e.target.select()} />
            </label>
            <div className="dialog-actions">
              <button type="button" className="primary-action" onClick={() => void (async () => {
                try {
                  await navigator.clipboard.writeText(shownKey);
                  onNotify(t("adm.copied"));
                } catch {
                  onNotify(t("adm.copyFail"), "error");
                }
              })()}>{t("adm.copy")}</button>
              <button type="button" className="action-btn" onClick={() => setShownKey(null)}>{t("adm.close")}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function FeedbackBackupView({ onNotify }: { onNotify: NotifyFn }) {
  const { locale, t } = useI18n();
  const [backups, setBackups] = useState<FeedbackBackupInfo[]>([]);
  const [settings, setSettings] = useState<FeedbackBackupSettings>({ backupCron: "", backupKeep: 5 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => { aliveRef.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await api.feedbackAdmin.backups();
      if (!aliveRef.current) return;
      setBackups(data.backups);
      setSettings(data.settings);
    } catch (err) {
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : t("adm.loadBackupsFail"));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const saveSettings = async (next: FeedbackBackupSettings) => {
    try {
      await api.feedbackAdmin.saveBackupSettings(next);
      setSettings(next);
      onNotify(t("adm.backupSettingsSaved"), "success");
    } catch (err) {
      onNotify(t("adm.saveSettingsFail"), "error");
    }
  };

  const restore = async (name: string) => {
    try {
      const res = await api.feedbackAdmin.restoreBackup(name);
      onNotify(res.restartRequired ? t("adm.restoreScheduled") : t("adm.restoredBackup"), "success");
    } catch (err) {
      onNotify(t("adm.restoreBackupFail"), "error");
    }
  };

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>{t("adm.retry")}</button>
      </div>
    );
  }
  if (loading) return <Loading />;

  const periodLabel = (cron: string): string => {
    if (cron === "") return t("adm.off");
    if (cron === "0 * * * *") return t("adm.everyHour");
    if (cron === "0 3 * * *") return t("adm.daily");
    if (cron === "0 3 * * 1") return t("adm.weekly");
    if (cron === "0 3 1 * *") return t("adm.monthly");
    return t("adm.custom", { cron });
  };

  return (
    <>
      <div className="admin-filters">
        <button className="admin-btn" type="button" onClick={() => void (async () => {
          try {
            await api.feedbackAdmin.createBackup();
            onNotify(t("adm.backupCreated"), "success");
            void load();
          } catch (err) {
            onNotify(t("adm.createBackupFail"), "error");
          }
        })()}>{t("adm.backupNow")}</button>
        <SDropdown
          items={[...BACKUP_CRONS, ...(settings.backupCron && !BACKUP_CRONS.includes(settings.backupCron) ? [settings.backupCron] : [])]}
          value={settings.backupCron}
          onChange={(backupCron) => void saveSettings({ ...settings, backupCron })}
          getKey={(cron) => cron || "off"}
          getLabel={(cron) => periodLabel(cron)}
          ariaLabel={t("adm.autoBackup")}
          className="admin-dropdown"
        />
        <SDropdown
          items={[1, 5, 10, 20, 50]}
          value={settings.backupKeep}
          onChange={(backupKeep) => void saveSettings({ ...settings, backupKeep })}
          getKey={(item) => item}
          getLabel={(item) => t("adm.keepN", { count: item })}
          ariaLabel={t("adm.keepCount")}
          className="admin-dropdown"
        />
      </div>

      <div className="admin-list">
        {backups.length === 0 ? (
          <div className="empty-state">{t("adm.noBackups")}</div>
        ) : (
          backups.map((b) => (
            <div className="admin-row" key={b.name}>
              <div className="admin-row-main">
                <strong>{b.name}</strong>
                <span className="admin-muted">{timeAgo(b.createdAt, locale)} · {(b.size / 1024).toFixed(0)} KB</span>
              </div>
              <div className="admin-row-actions">
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn" type="button">{t("adm.restore")}</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">{t("adm.restoreTitle", { name: b.name })}</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">
                        {t("adm.restoreDesc")}
                      </AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">{t("adm.cancel")}</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void restore(b.name)}>{t("adm.restore")}</button>
                        </AlertDialog.Action>
                      </div>
                    </AlertDialog.Content>
                  </AlertDialog.Portal>
                </AlertDialog.Root>
              </div>
            </div>
          ))
        )}
      </div>
    </>
  );
}
