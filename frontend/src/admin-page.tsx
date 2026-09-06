import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { UserMenu } from "./user-menu";
import { MobileMenu } from "./mobile-menu";
import { Loading } from "./loading";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type AdminStats, type AdminUser, type BoardSummary, type BoardVisibility, type DeletedDiscussion, type DeletedReply, type FeedbackApiKey, type FeedbackBackupInfo, type FeedbackBackupSettings, type FeedbackProjectAdmin, type FeedbackProjectMember, type ModerationAction, type ReportDTO, type UserRole, type UserStatus } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatTime } from "./lib/format";
import { useEscapeKey, useModalScrollLock } from "./lib/use-modal-scroll-lock";

type AdminSection = "dashboard" | "users" | "boards" | "moderation" | "audit" | "feedback";

const sections: { id: AdminSection; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "users", label: "Users" },
  { id: "boards", label: "Boards" },
  { id: "moderation", label: "Moderation" },
  { id: "audit", label: "Audit log" },
  { id: "feedback", label: "Feedback" },
];

const STATUS_PILLS: { key: UserStatus | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "active", label: "Active" },
  { key: "banned", label: "Banned" },
  { key: "deactivated", label: "Deactivated" },
];

const ROLE_OPTIONS: { key: UserRole | "all"; label: string }[] = [
  { key: "all", label: "All roles" },
  { key: "student", label: "Student" },
  { key: "admin", label: "Admin" },
];

function SearchIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.7" /><path d="M16 16L21 21" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
}

function Badge({ children, variant }: { children: React.ReactNode; variant: string }) {
  return <span className={`admin-badge ${variant}`}>{children}</span>;
}

export function AdminPage({ onNotify }: { onNotify: (message: string) => void }) {
  const { user, loading } = useAuth();
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
            <div className="empty-state">Sign in to access the admin panel. <a className="sender" href="/login">Sign in</a></div>
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
            <div className="empty-state">You don’t have permission to access the admin panel.</div>
          </section>
        </main>
      </Shell>
    );
  }

  return (
    <Shell>
      <main className="shell admin-layout">
        <aside className="settings-sidebar">
          <h1>Admin</h1>
          <nav className="settings-nav" aria-label="Admin sections" ref={adminNavRef}>
            {sections.map((item) => (
              <button data-admin-section={item.id} className={selectedSection === item.id ? "active" : ""} key={item.id} type="button" aria-current={selectedSection === item.id ? "page" : undefined} onClick={() => switchSection(item.id)}>
                <span>{item.label}</span>
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
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : "Could not load stats.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>Try again</button>
      </div>
    );
  }
  if (!stats) return <Loading />;

  return (
    <div className="content-fade">
      <header><h2>Dashboard</h2><p>Site-wide numbers at a glance.</p></header>
      <StatGrid title="Users" stats={[["Total", stats.users.total], ["Pending", stats.users.pending], ["Active", stats.users.active], ["Banned", stats.users.banned], ["Deactivated", stats.users.deactivated]]} />
      <StatGrid title="Content" stats={[["Discussions", stats.content.discussions], ["Replies", stats.content.replies], ["Boards", stats.content.boards]]} />
      <StatGrid title="Moderation" stats={[["Open reports", stats.moderation.openReports], ["Active bans", stats.moderation.activeBans]]} />
      <StatGrid title="Activity today" stats={[["Active authors", stats.activity.activeToday], ["New users", stats.activity.newUsersToday], ["New discussions", stats.activity.newDiscussionsToday], ["New replies", stats.activity.newRepliesToday], ["Online now", stats.activity.onlineNow]]} />
      <p className="community-note">Dashboard figures update on refresh. Pending users can be verified in the Users tab.</p>
    </div>
  );
}

// ---------------------------------------------------------------- users

function UsersSection({ onNotify }: { onNotify: (message: string) => void }) {
  const { user: me } = useAuth();
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
      setError(err instanceof ApiError ? err.message : "Could not load users.");
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
      .catch((err) => { if (alive) setError(err instanceof ApiError ? err.message : "Could not load users."); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [debouncedQuery, status, role]);

  const runAction = async (user: AdminUser, fn: () => Promise<unknown>, success: string) => {
    setBusyId(user.id);
    try {
      await fn();
      await loadFirst();
      onNotify(success);
    } catch (err) {
      onNotify(err instanceof ApiError ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  };

  const changeUserStatus = (user: AdminUser, next: UserStatus) => {
    if (next === user.status) return;
    if (next === "banned") {
      onNotify("Ban status is managed in Moderation.");
      return;
    }
    if (next === "pending") {
      onNotify("Pending status is managed by verification.");
      return;
    }
    const action = next === "active" && user.status === "banned"
      ? () => api.moderation.unban(user.username)
      : next === "active" && user.status === "pending"
        ? () => api.admin.verifyUser(user.id)
        : () => api.admin.changeStatus(user.id, { status: next });
    void runAction(user, action, `User status changed to ${next}.`);
  };

  return (
    <>
      <header><h2>Users</h2><p>Manage accounts, roles, and status.</p></header>

      <div className="admin-filters">
        <label className="search-field admin-search">
          <SearchIcon />
          <span className="sr-only">Search users</span>
          <input type="search" placeholder="Search by username, name, or email" autoComplete="off" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <div className="admin-pills" role="group" aria-label="Filter by status">
          {STATUS_PILLS.map((pill) => (
            <button className={`admin-pill ${status === pill.key ? "active" : ""}`} type="button" key={pill.key} onClick={() => setStatus(pill.key)}>{pill.label}</button>
          ))}
        </div>
        <SDropdown
          items={ROLE_OPTIONS}
          value={ROLE_OPTIONS.find((option) => option.key === role) ?? null}
          onChange={(option) => setRole(option.key)}
          getKey={(option) => option.key}
          getLabel={(option) => option.label}
          ariaLabel="Filter by role"
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
                  <Badge variant={user.role}>{user.role}</Badge>
                  <Badge variant={user.status}>{user.status}</Badge>
                  {user.banActive && <Badge variant="banned">ban active</Badge>}
                  {!user.emailVerified && <Badge variant="pending">unverified</Badge>}
                  <span className="admin-muted">joined {formatTime(user.createdAt)}</span>
                </div>
              </div>
              <div className="admin-row-actions" data-busy={busyId === user.id || undefined} aria-label={`Actions for ${user.displayName}`}>
                <SDropdown
                  items={["student", "admin"] as UserRole[]}
                  value={user.role}
                  onChange={(nextRole) => void runAction(user, () => api.admin.changeRole(user.id, { role: nextRole }), "Role updated.")}
                  getKey={(item) => item}
                  getLabel={(item) => item}
                  label="Role"
                  ariaLabel={`Role for ${user.displayName}`}
                  className="admin-control admin-dropdown"
                  disabled={busyId !== null || user.id === me?.id}
                />
                <SDropdown
                  items={["pending", "active", "banned", "deactivated"] as UserStatus[]}
                  value={user.status}
                  onChange={(nextStatus) => changeUserStatus(user, nextStatus)}
                  getKey={(item) => item}
                  getLabel={(item) => item}
                  label="Status"
                  ariaLabel={`Status for ${user.displayName}`}
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
                      onNotify(err instanceof ApiError ? err.message : "Action failed.");
                    } finally {
                      setBusyId(null);
                    }
                  })()}>Reset password</button>
                )}
                {user.id !== me?.id && (
                  <AlertDialog.Root>
                    <AlertDialog.Trigger asChild>
                      <button className="admin-btn danger" type="button" disabled={busyId !== null}>Delete</button>
                    </AlertDialog.Trigger>
                    <AlertDialog.Portal>
                      <AlertDialog.Overlay className="dialog-overlay" />
                      <AlertDialog.Content className="dialog-content">
                        <AlertDialog.Title className="dialog-title">Delete {user.displayName}?</AlertDialog.Title>
                        <AlertDialog.Description className="dialog-description">Their posts stay in place, but this account is deactivated and anonymized. This can’t be undone.</AlertDialog.Description>
                        <div className="dialog-actions">
                          <AlertDialog.Cancel asChild><button type="button" className="action-btn">Cancel</button></AlertDialog.Cancel>
                          <AlertDialog.Action asChild><button type="button" className="dialog-danger" onClick={() => void runAction(user, () => api.admin.deleteUser(user.id), "User deleted.")}>Delete</button></AlertDialog.Action>
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
            onNotify(err instanceof ApiError ? err.message : "Could not load more.");
          }
        })()}>Load more</button>
      )}
      {!loading && items.length === 0 && <div className="empty-state">No users found.</div>}
      {temporaryPassword && (
        <div className="dialog-overlay">
          <div className="dialog-content feedback-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <h2 className="dialog-title">Temporary password created</h2>
            <p className="admin-muted">Copy it now — it is only shown once.</p>
            <label className="form-field"><span>Temporary password</span><input readOnly value={temporaryPassword} onFocus={(event) => event.target.select()} /></label>
            <div className="dialog-actions">
              <button className="primary-action" type="button" onClick={() => void (async () => {
                try {
                  await navigator.clipboard.writeText(temporaryPassword);
                  onNotify("Copied to clipboard.");
                } catch {
                  onNotify("Copy failed — select the text and copy it manually.");
                }
              })()}>Copy</button>
              <button className="action-btn" type="button" onClick={() => setTemporaryPassword(null)}>Close</button>
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

function BoardsSection({ onNotify }: { onNotify: (message: string) => void }) {
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
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : "Could not load boards.");
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const flash = (message: string) => {
    onNotify(message);
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
      flash("Board created.");
      await load();
    } catch (err) {
      flash(err instanceof ApiError ? err.message : "Could not create board.");
    } finally {
      setCreateBusy(false);
    }
  };

  const deleteBoard = async (board: BoardSummary) => {
    try {
      await api.boards.del(board.slug, { reason: "admin delete" });
      flash("Board deleted.");
      await load();
    } catch (err) {
      flash(err instanceof ApiError ? err.message : "Could not delete board.");
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
      flash(err instanceof ApiError ? err.message : "Could not load members.");
    }
  };

  return (
    <>
      <header><h2>Boards</h2><p>Create, edit, and organize boards.</p></header>

      {error && <div className="empty-state">{error}</div>}

      <div className="admin-create-toggle">
        <button className="admin-btn" type="button" onClick={() => setCreateOpen((v) => !v)}>{createOpen ? "Cancel" : "Create board"}</button>
      </div>
      {createOpen && (
        <form className="admin-inline-form" onSubmit={createBoard} noValidate>
          <label><span>Name</span><input value={createName} onChange={(e) => setCreateName(e.target.value)} maxLength={60} required /></label>
          <label><span>Slug</span><input value={createSlug} onChange={(e) => setCreateSlug(e.target.value)} pattern="[a-z0-9-]+" maxLength={50} required placeholder="campus-life" /></label>
          <label><span>Description</span><input value={createDesc} onChange={(e) => setCreateDesc(e.target.value)} maxLength={500} /></label>
          <div className="admin-inline-selects">
            <SDropdown
              items={VISIBILITIES}
              value={createVisibility}
              onChange={(value) => setCreateVisibility(value)}
              getKey={(item) => item}
              getLabel={(item) => item}
              label="Visibility"
              ariaLabel="Board visibility"
              className="admin-dropdown"
            />
            <SDropdown
              items={POSTING_POLICIES}
              value={createPosting}
              onChange={(value) => setCreatePosting(value)}
              getKey={(item) => item}
              getLabel={(item) => item}
              label="Posting"
              ariaLabel="Board posting policy"
              className="admin-dropdown"
            />
          </div>
          <button className="primary-action" type="submit" disabled={createBusy || !createName.trim() || !createSlug.trim()}>{createBusy ? "Creating…" : "Create board"}</button>
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
                  <Badge variant={board.visibility}>{board.visibility}</Badge>
                  <Badge variant="active">{board.postingPolicy}</Badge>
                  <span className="admin-muted">{board.memberCount} members · {board.todayActivity} today</span>
                </div>
              </div>
              <div className="admin-row-actions">
                <button className="admin-btn" type="button" onClick={() => { setEditingSlug(editingSlug === board.slug ? null : board.slug); setMembersSlug(null); }}>{editingSlug === board.slug ? "Done" : "Edit"}</button>
                <button className="admin-btn" type="button" onClick={() => void toggleMembers(board.slug)}>{membersSlug === board.slug ? "Hide members" : "Members"}</button>
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn danger" type="button">Delete</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">Delete board “{board.name}”?</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">
                        This hides the board and all of its discussions.
                      </AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">Cancel</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void deleteBoard(board)}>Delete</button>
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
                            flash("Member role updated.");
                          } catch (err) {
                            flash(err instanceof ApiError ? err.message : "Could not update member role.");
                          }
                        })()}
                        getKey={(item) => item}
                        getLabel={(item) => item}
                        ariaLabel={`Role for ${member.handle}`}
                        className="member-role admin-dropdown"
                      />
                    </div>
                  ))}
                  {membersMap[board.slug]?.length === 0 && <p className="admin-muted">No members yet.</p>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {!loading && boards.length === 0 && <div className="empty-state">No boards found.</div>}
    </>
  );
}

function BoardEditForm({ board, onDone, onError }: { board: BoardSummary; onDone: () => void; onError: (message: string) => void }) {
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
      onError(err instanceof ApiError ? err.message : "Could not save board.");
      setBusy(false);
    }
  };

  return (
    <form className="admin-inline-form" onSubmit={save} noValidate>
      <label><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required /></label>
      <label><span>Description</span><input value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={500} /></label>
      <div className="admin-inline-selects">
        <SDropdown items={VISIBILITIES} value={visibility} onChange={(value) => setVisibility(value)} getKey={(item) => item} getLabel={(item) => item} label="Visibility" ariaLabel="Board visibility" className="admin-dropdown" />
        <SDropdown items={POSTING_POLICIES} value={posting} onChange={(value) => setPosting(value)} getKey={(item) => item} getLabel={(item) => item} label="Posting" ariaLabel="Board posting policy" className="admin-dropdown" />
      </div>
      <button className="primary-action" type="submit" disabled={busy || !name.trim()}>{busy ? "Saving…" : "Save board"}</button>
    </form>
  );
}

// ---------------------------------------------------------------- moderation

function ModerationSection({ onNotify }: { onNotify: (message: string) => void }) {
  const [tab, setTab] = useState<"reports" | "deleted">("reports");
  return (
    <>
      <header className="admin-section-header"><h2>Moderation</h2></header>
      <div className="admin-pills admin-section-tabs" role="tablist" aria-label="Moderation views">
        <button className={`admin-pill ${tab === "reports" ? "active" : ""}`} type="button" role="tab" aria-selected={tab === "reports"} onClick={() => setTab("reports")}>Open reports</button>
        <button className={`admin-pill ${tab === "deleted" ? "active" : ""}`} type="button" role="tab" aria-selected={tab === "deleted"} onClick={() => setTab("deleted")}>Deleted content</button>
      </div>
      {tab === "reports" ? <ReportsList onNotify={onNotify} /> : <DeletedList onNotify={onNotify} />}
    </>
  );
}

function ReportsList({ onNotify }: { onNotify: (message: string) => void }) {
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
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : "Could not load reports.");
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
      onNotify(success);
    } catch (err) {
      onNotify(err instanceof ApiError ? err.message : "Action failed.");
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
            const t = report.target;
            const banUsername = t?.type === "user" ? t.username ?? null : null;
            return (
              <div className="admin-row" key={report.id}>
                <div className="admin-row-main">
                  <strong>{t?.title ?? t?.displayName ?? t?.handle ?? t?.username ?? `#${report.reportableId}`}</strong>
                  {href && <a className="sender" href={href}>view</a>}
                  <span className="admin-muted">{report.reason || "No reason given"} · reported by @{report.reporter.handle} · {formatTime(report.createdAt)}</span>
                </div>
                <div className="admin-row-actions">
                  <button className="admin-btn" type="button" disabled={busyId !== null} onClick={() => void run(report, () => api.moderation.resolveReport(report.id, { status: "in_progress", action: "report.in_progress" }), "Marked in progress.")}>In progress</button>
                  <button className="admin-btn" type="button" disabled={busyId !== null} onClick={() => void run(report, () => api.moderation.resolveReport(report.id, { status: "resolved", action: "report.resolved" }), "Report resolved.")}>Resolve</button>
                  <button className="admin-btn" type="button" disabled={busyId !== null} onClick={() => void run(report, () => api.moderation.resolveReport(report.id, { status: "dismissed", action: "report.dismissed" }), "Report dismissed.")}>Dismiss</button>
                  {t?.type === "user" && (
                    <button className="admin-btn danger" type="button" disabled={busyId !== null || !banUsername} onClick={() => { if (banUsername) void run(report, () => api.moderation.ban({ username: banUsername }), "User banned."); }}>Ban</button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {!loading && items.length === 0 && <div className="empty-state">No open reports.</div>}
    </>
  );
}

function DeletedList({ onNotify }: { onNotify: (message: string) => void }) {
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
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : "Could not load deleted content.");
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
      onNotify("Content restored.");
    } catch (err) {
      onNotify(err instanceof ApiError ? err.message : "Could not restore.");
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
          <p className="admin-group-label">Deleted discussions</p>
          <div className="admin-list">
            {discussions.map((d) => (
              <div className="admin-row" key={d.id}>
                <div className="admin-row-main">
                  <strong>{d.title}</strong>
                  <span className="admin-muted">{d.preview} · /{d.boardSlug}</span>
                  <div className="admin-row-tags">
                    {d.deletedBy && <span className="admin-muted">by @{d.deletedBy.handle}</span>}
                    <span className="admin-muted">{formatTime(d.deletedAt)}</span>
                  </div>
                </div>
                <div className="admin-row-actions">
                  <button className="admin-btn" type="button" disabled={busyKey !== null} onClick={() => void restore("discussion", d.id)}>Restore</button>
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
                onNotify(err instanceof ApiError ? err.message : "Could not load more.");
              }
            })()}>Load more discussions</button>
          )}

          <p className="admin-group-label">Deleted replies</p>
          <div className="admin-list">
            {replies.map((r) => (
              <div className="admin-row" key={r.id}>
                <div className="admin-row-main">
                  <strong>{r.discussionTitle || "Reply"}</strong>
                  <span className="admin-muted">{r.preview}</span>
                  <div className="admin-row-tags">
                    <a className="sender" href={`/d/${r.discussionId}`}>view thread</a>
                    <span className="admin-muted">{formatTime(r.deletedAt)}</span>
                  </div>
                </div>
                <div className="admin-row-actions">
                  <button className="admin-btn" type="button" disabled={busyKey !== null} onClick={() => void restore("reply", r.id)}>Restore</button>
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
                onNotify(err instanceof ApiError ? err.message : "Could not load more.");
              }
            })()}>Load more replies</button>
          )}
          {discussions.length === 0 && replies.length === 0 && <div className="empty-state">No deleted content.</div>}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- audit

function AuditSection() {
  const [items, setItems] = useState<ModerationAction[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.moderation
      .actions({ limit: 30 })
      .then((data) => { if (!alive) return; setItems(data.items); setNextCursor(data.nextCursor ? Number(data.nextCursor) : null); })
      .catch((err) => { if (alive) setError(err instanceof ApiError ? err.message : "Could not load audit log."); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  return (
    <>
      <header><h2>Audit log</h2><p>Every moderation and admin action, most recent first.</p></header>
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
              <div className="admin-row-tags"><span className="admin-muted">{formatTime(action.createdAt)}</span></div>
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
            setError(err instanceof ApiError ? err.message : "Could not load more.");
          }
        })()}>Load more</button>
      )}
      {!loading && items.length === 0 && <div className="empty-state">No moderation actions yet.</div>}
    </>
  );
}

// ---------------------------------------------------------------- shell

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="topbar">
        <div className="shell topbar-inner">
          <a href="/" className="wordmark" aria-label="Samryetha home">Samryetha</a>
          <nav className="primary-nav" aria-label="Primary navigation">
            <a className="nav-link" href="/" data-view="latest">Latest</a>
            <a className="nav-link" href="/" data-view="followed">Followed</a>
            <a className="nav-link" href="/" data-view="boards">Boards</a>
          </nav>
          <div className="actions">
            <label className="search-field">
              <SearchIcon />
              <span className="sr-only">Search discussions</span>
              <input type="search" placeholder="Search discussions" autoComplete="off" />
            </label>
            <MobileMenu />
            <UserMenu current="admin" />
            <a className="compose" href="/post">Post</a>
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

const BACKUP_PERIODS: [string, string][] = [
  ["", "Off"],
  ["0 * * * *", "Every hour"],
  ["0 3 * * *", "Daily (3am)"],
  ["0 3 * * 1", "Weekly (Mon 3am)"],
  ["0 3 1 * *", "Monthly (1st 3am)"],
];

function FeedbackSection({ onNotify }: { onNotify: (message: string) => void }) {
  const [tab, setTab] = useState<FeedbackTab>("projects");
  return (
    <div className="content-fade">
      <header className="admin-section-header"><h2>Feedback</h2></header>
      <div className="admin-pills admin-section-tabs" role="tablist" aria-label="Feedback admin views">
        {([["projects", "Projects"], ["keys", "Agent keys"], ["backup", "Backup"]] as [FeedbackTab, string][]).map(([id, label]) => (
          <button key={id} className={`admin-pill ${tab === id ? "active" : ""}`} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      {tab === "projects" && <FeedbackProjectsView onNotify={onNotify} />}
      {tab === "keys" && <FeedbackKeysView onNotify={onNotify} />}
      {tab === "backup" && <FeedbackBackupView onNotify={onNotify} />}
    </div>
  );
}

function FeedbackProjectsView({ onNotify }: { onNotify: (message: string) => void }) {
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
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : "Could not load projects.");
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
      setFormError("Project name is required.");
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
      onNotify("Project saved.");
      void load();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Failed to save project.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (p: FeedbackProjectAdmin) => {
    try {
      await api.feedbackAdmin.delProject(p.id);
      setDeleting(null);
      onNotify("Project deleted.");
      void load();
    } catch (err) {
      onNotify(err instanceof ApiError ? err.message : "Failed to delete project.");
      setDeleting(null);
    }
  };

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>Try again</button>
      </div>
    );
  }
  if (loading) return <Loading />;

  return (
    <>
      <div className="view-head" style={{ marginTop: 8 }}>
        <p className="admin-muted">Members can submit feedback; programmers can also mark items done or expired.</p>
        <button className="admin-btn" type="button" onClick={openCreate}>New project</button>
      </div>

      <div className="admin-list">
        {projects.length === 0 ? (
          <div className="empty-state">No projects yet. Create one to get started.</div>
        ) : (
          projects.map((p) => (
            <div className="admin-row admin-row-stacked" key={p.id}>
              <div className="admin-row-main">
                <strong>{p.name}</strong>
                <span className="admin-muted">{p.description || "—"}</span>
                <div className="admin-row-tags">
                  <span className="admin-muted">
                    {p.members.length
                      ? p.members.map((m) => `${m.handle}${m.isProgrammer ? " (programmer)" : ""}`).join(", ")
                      : "No members"}
                  </span>
                </div>
              </div>
              <div className="admin-row-actions">
                <button className="admin-btn" type="button" onClick={() => openEdit(p)}>Edit</button>
                <AlertDialog.Root open={deleting?.id === p.id} onOpenChange={(o) => !o && setDeleting(null)}>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn danger" type="button" onClick={() => setDeleting(p)}>Delete</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">Delete project “{p.name}”?</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">
                        This also deletes all feedback in the project. This can’t be undone.
                      </AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">Cancel</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void remove(p)}>Delete</button>
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
            <h2 className="dialog-title">{editing ? `Edit project` : "New project"}</h2>
            <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
              <label className="form-field">
                <span>Name</span>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={64} autoFocus />
              </label>
              <label className="form-field">
                <span>Description</span>
                <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={500} rows={3} />
              </label>
              <h4 className="sub-title">Members (check to add; “programmer” can manage items)</h4>
              <div className="member-picker">
                {userOptions.length === 0 ? (
                  <div className="admin-muted">No active users.</div>
                ) : (
                  userOptions.map((u) => {
                    const flag = flags[u.id] ?? { member: false, programmer: false };
                    return (
                      <div className="member-row" key={u.id}>
                        <label><input type="checkbox" checked={flag.member} onChange={(e) => setFlags((prev) => ({ ...prev, [u.id]: { member: e.target.checked, programmer: flag.programmer } }))} /> @{u.handle}</label>
                        <label className="muted"><input type="checkbox" disabled={!flag.member} checked={flag.member && flag.programmer} onChange={(e) => setFlags((prev) => ({ ...prev, [u.id]: { member: true, programmer: e.target.checked } }))} /> Programmer</label>
                      </div>
                    );
                  })
                )}
              </div>
              {formError && <div className="dialog-error">{formError}</div>}
              <div className="dialog-actions">
                <button type="button" className="action-btn" onClick={() => setModalOpen(false)}>Cancel</button>
                <button type="submit" className="primary-action" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

function FeedbackKeysView({ onNotify }: { onNotify: (message: string) => void }) {
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
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : "Could not load API keys.");
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
      setFormError("Key name is required.");
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
      setFormError(err instanceof ApiError ? err.message : "Failed to create key.");
    } finally {
      setCreating(false);
    }
  };

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>Try again</button>
      </div>
    );
  }
  if (loading) return <Loading />;

  return (
    <>
      <div className="view-head" style={{ marginTop: 8 }}>
        <p className="admin-muted">Keys let AI / curl read tasks and (with write role) mark them done. Unchecking all projects = access to all.</p>
        <button className="admin-btn" type="button" onClick={() => { setForm({ name: "", role: "read" }); setScopedIds([]); setFormError(""); setCreateOpen(true); }}>New key</button>
      </div>

      <div className="admin-list">
        {keys.length === 0 ? (
          <div className="empty-state">No API keys yet.</div>
        ) : (
          keys.map((k) => (
            <div className="admin-row admin-row-stacked" key={k.id}>
              <div className="admin-row-main">
                <strong>{k.name} <span className="admin-badge bug">#{k.prefix}…</span> <Badge variant={k.role}>{k.role}</Badge> <Badge variant={k.enabled ? "done" : "expired"}>{k.enabled ? "Enabled" : "Disabled"}</Badge></strong>
                <span className="admin-muted">
                  Scope: {k.projectIds.length ? k.projectIds.map((id) => projects.find((p) => p.id === id)?.name ?? `#${id}`).join(", ") : "All projects"}
                  {k.lastUsedAt ? ` · last used ${formatTime(k.lastUsedAt)}` : ""}
                </span>
              </div>
              <div className="admin-row-actions">
                <button className="admin-btn" type="button" onClick={() => void (async () => {
                  try {
                    await api.feedbackAdmin.setKeyEnabled(k.id, !k.enabled);
                    void load();
                  } catch (err) {
                    onNotify(err instanceof ApiError ? err.message : "Failed to toggle key.");
                  }
                })()}>{k.enabled ? "Disable" : "Enable"}</button>
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn danger" type="button">Delete</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">Delete key “{k.name}”?</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">The key stops working immediately.</AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">Cancel</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void (async () => {
                            try {
                              await api.feedbackAdmin.delKey(k.id);
                              void load();
                            } catch (err) {
                              onNotify(err instanceof ApiError ? err.message : "Failed to delete key.");
                            }
                          })()}>Delete</button>
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
            <h2 className="dialog-title">New Agent API key</h2>
            <form onSubmit={(e) => { e.preventDefault(); void create(); }}>
              <label className="form-field">
                <span>Name</span>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={64} placeholder="e.g. my-agent" autoFocus />
              </label>
              <SDropdown
                items={["read", "write"] as ("read" | "write")[]}
                value={form.role}
                onChange={(role) => setForm({ ...form, role })}
                getKey={(item) => item}
                getLabel={(item) => item === "read" ? "Read only" : "Read + mark done"}
                label="Role"
                ariaLabel="Agent API key role"
                className="form-dropdown"
              />
              <h4 className="sub-title">Accessible projects (none selected = all)</h4>
              <div className="member-picker">
                {projects.length === 0 ? (
                  <div className="admin-muted">No projects.</div>
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
                <button type="button" className="action-btn" onClick={() => setCreateOpen(false)}>Cancel</button>
                <button type="submit" className="primary-action" disabled={creating}>{creating ? "Creating…" : "Create"}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {shownKey && (
        <div className="dialog-overlay" onClick={() => setShownKey(null)}>
          <div className="dialog-content feedback-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h2 className="dialog-title">API key created</h2>
            <p className="admin-muted">Copy it now — it’s only shown once. Use it as <code>X-Api-Key: &lt;key&gt;</code> against <code>/api/agent/v1/tasks</code>.</p>
            <label className="form-field">
              <span>API key</span>
              <textarea readOnly value={shownKey} rows={2} onFocus={(e) => e.target.select()} />
            </label>
            <div className="dialog-actions">
              <button type="button" className="primary-action" onClick={() => void (async () => {
                try {
                  await navigator.clipboard.writeText(shownKey);
                  onNotify("Copied to clipboard.");
                } catch {
                  onNotify("Copy failed — select the text and copy it manually.");
                }
              })()}>Copy</button>
              <button type="button" className="action-btn" onClick={() => setShownKey(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function FeedbackBackupView({ onNotify }: { onNotify: (message: string) => void }) {
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
      if (aliveRef.current) setError(err instanceof ApiError ? err.message : "Could not load backups.");
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
      onNotify("Backup settings saved.");
    } catch (err) {
      onNotify(err instanceof ApiError ? err.message : "Failed to save settings.");
    }
  };

  const restore = async (name: string) => {
    try {
      const res = await api.feedbackAdmin.restoreBackup(name);
      onNotify(res.restartRequired ? "Restore scheduled — restart the server to apply." : "Restored.");
    } catch (err) {
      onNotify(err instanceof ApiError ? err.message : "Failed to restore.");
    }
  };

  if (error) {
    return (
      <div className="empty-state">
        {error}
        <button className="admin-btn" type="button" onClick={() => void load()}>Try again</button>
      </div>
    );
  }
  if (loading) return <Loading />;

  return (
    <>
      <div className="admin-filters">
        <button className="admin-btn" type="button" onClick={() => void (async () => {
          try {
            await api.feedbackAdmin.createBackup();
            onNotify("Backup created.");
            void load();
          } catch (err) {
            onNotify(err instanceof ApiError ? err.message : "Failed to create backup.");
          }
        })()}>Back up now</button>
        <SDropdown
          items={[...BACKUP_PERIODS, ...(settings.backupCron && !BACKUP_PERIODS.some(([cron]) => cron === settings.backupCron) ? [[settings.backupCron, `Custom: ${settings.backupCron}`] as [string, string]] : [])]}
          value={BACKUP_PERIODS.find(([cron]) => cron === settings.backupCron) ?? ([settings.backupCron, `Custom: ${settings.backupCron}`] as [string, string])}
          onChange={([backupCron]) => void saveSettings({ ...settings, backupCron })}
          getKey={([cron]) => cron || "off"}
          getLabel={([, label]) => label}
          ariaLabel="Auto backup"
          className="admin-dropdown"
        />
        <SDropdown
          items={[1, 5, 10, 20, 50]}
          value={settings.backupKeep}
          onChange={(backupKeep) => void saveSettings({ ...settings, backupKeep })}
          getKey={(item) => item}
          getLabel={(item) => `Keep ${item} backups`}
          ariaLabel="Keep count"
          className="admin-dropdown"
        />
      </div>

      <div className="admin-list">
        {backups.length === 0 ? (
          <div className="empty-state">No backups yet.</div>
        ) : (
          backups.map((b) => (
            <div className="admin-row" key={b.name}>
              <div className="admin-row-main">
                <strong>{b.name}</strong>
                <span className="admin-muted">{formatTime(b.createdAt)} · {(b.size / 1024).toFixed(0)} KB</span>
              </div>
              <div className="admin-row-actions">
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button className="admin-btn" type="button">Restore</button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="dialog-overlay" />
                    <AlertDialog.Content className="dialog-content">
                      <AlertDialog.Title className="dialog-title">Restore “{b.name}”?</AlertDialog.Title>
                      <AlertDialog.Description className="dialog-description">
                        Current data will be replaced. The restore applies on the next server restart.
                      </AlertDialog.Description>
                      <div className="dialog-actions">
                        <AlertDialog.Cancel asChild>
                          <button type="button" className="action-btn">Cancel</button>
                        </AlertDialog.Cancel>
                        <AlertDialog.Action asChild>
                          <button type="button" className="dialog-danger" onClick={() => void restore(b.name)}>Restore</button>
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
