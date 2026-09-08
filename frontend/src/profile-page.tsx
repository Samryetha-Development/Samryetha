import { useEffect, useMemo, useRef, useState } from "react";
import { ThreadRow } from "./thread-row";
import { Loading } from "./loading";
import { AppShell } from "./app-shell";
import { useAnimatedTabs } from "./lib/use-animated-tabs";
import { useTabIndicator } from "./lib/use-tab-indicator";
import { api, ApiError, type PublicProfile, type ReplyFeedItem, type ThreadSummary } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatDateL, useI18n } from "./lib/i18n";
import { initials } from "./lib/format";

type ProfileTab = "posts" | "replies" | "saved";

const profileTabKeys = { posts: "profile.posts", replies: "profile.replies", saved: "profile.saved" } as const;

export function ProfilePage() {
  const { user, loading: authLoading } = useAuth();
  const { locale, t } = useI18n();
  const [profile, setProfile] = useState<PublicProfile | null>(null);
  const [loadingProfile, setLoadingProfile] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [followBusy, setFollowBusy] = useState(false);
  const [followError, setFollowError] = useState<string | null>(null);
  const { active: selectedTab, committed: tab, phase: panelPhase, setActive: switchTab } = useAnimatedTabs<ProfileTab>({ initial: "posts", duration: 95 });
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [replies, setReplies] = useState<ReplyFeedItem[]>([]);
  const [loadingItems, setLoadingItems] = useState(true);
  const [tabError, setTabError] = useState<string | null>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const indicator = useTabIndicator(tabsRef, (t) => `[data-profile-tab="${t}"]`, selectedTab);

  const requested = useMemo(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("username");
  }, []);

  const targetUsername = requested ?? user?.username ?? null;
  const isSelf = Boolean(user && (!requested || requested === user.username));

  useEffect(() => {
    let alive = true;
    setLoadingProfile(true);
    setProfileError(null);
    if (!targetUsername) {
      setProfile(null);
      setLoadingProfile(false);
      return;
    }
    api.users
      .get(targetUsername)
      .then((data) => {
        if (alive) setProfile(data);
      })
      .catch(() => {
        if (alive) setProfileError(t("profile.loadFail"));
      })
      .finally(() => {
        if (alive) setLoadingProfile(false);
      });
    return () => {
      alive = false;
    };
  }, [targetUsername]);

  useEffect(() => {
    let alive = true;
    setLoadingItems(true);
    setTabError(null);
    if (!targetUsername) {
      setLoadingItems(false);
      return;
    }
    const load = async () => {
      try {
        if (tab === "posts") {
          const data = await api.users.posts(targetUsername);
          if (alive) setThreads(data.items);
        } else if (tab === "replies") {
          const data = await api.users.replies(targetUsername);
          if (alive) setReplies(data.items);
        } else {
          const data = await api.users.saved(targetUsername);
          if (alive) setThreads(data.items);
        }
      } catch {
        if (alive) setTabError(tab === "saved" ? t("profile.savedPrivate") : t("profile.tabFail"));
      } finally {
        if (alive) setLoadingItems(false);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, [targetUsername, tab]);

  // 未登录点击关注 → 跳转登录页（一致模式）
  const promptLogin = () => {
    window.location.href = "/login";
  };

  const followUser = async () => {
    if (!profile || followBusy) return;
    setFollowBusy(true);
    setFollowError(null);
    try {
      if (profile.isFollowing) {
        await api.users.unfollow(profile.username);
        setProfile({ ...profile, isFollowing: false, stats: { ...profile.stats, followers: profile.stats.followers - 1 } });
      } else {
        await api.users.follow(profile.username);
        setProfile({ ...profile, isFollowing: true, stats: { ...profile.stats, followers: profile.stats.followers + 1 } });
      }
    } catch (err) {
      setFollowError(err instanceof ApiError ? err.message : t("profile.followFail"));
    } finally {
      setFollowBusy(false);
    }
  };

  const displayName = profile?.displayName ?? (user?.displayName ?? t("common.loading"));
  const handle = profile?.handle ?? targetUsername;
  const bio = profile?.bio ?? "";

  return (
    <AppShell current="profile">
      <main className="shell profile-layout">
        <section className="profile-main" aria-labelledby="profile-name">
          {!targetUsername ? (
            authLoading ? (
              <Loading />
            ) : (
              <div className="empty-state content-fade">{t("profile.signInToView")} <a className="sender" href="/login">{t("profile.signIn")}</a></div>
            )
          ) : loadingProfile ? (
            <Loading />
          ) : profileError ? (
            <div className="empty-state content-fade">{profileError} {!user && <a className="sender" href="/login">{t("profile.signIn")}</a>}</div>
          ) : (
            <div className="content-fade">
              <div className="profile-identity">
                <div className="profile-avatar" aria-hidden="true">{initials(displayName)}<span /></div>
                <div className="profile-copy">
                  <h1 id="profile-name">{displayName}</h1>
                  <p className="profile-handle">@{handle}</p>
                  {bio && <p className="profile-bio">{bio}</p>}
                </div>
                {isSelf ? (
                  <div className="profile-actions">
                    <a className="edit-profile" href="/settings">{t("profile.editProfile")}</a>
                  </div>
                ) : (
                  <div className="profile-actions">
                    <a className="edit-profile" href={`/inbox?to=${encodeURIComponent(profile?.username ?? targetUsername ?? "")}`}>{t("profile.message")}</a>
                    <button className={`edit-profile ${profile?.isFollowing ? "following" : ""}`} type="button" disabled={followBusy} onClick={() => (user ? void followUser() : promptLogin())}>
                      {t(profile?.isFollowing ? "profile.following" : "profile.follow")}
                    </button>
                  </div>
                )}
                {followError && <p className="form-error" role="alert">{followError}</p>}
              </div>

              <dl className="profile-stats">
                <div><dt>{t("profile.discussions")}</dt><dd>{profile?.stats.discussions ?? 0}</dd></div>
                <div><dt>{t("profile.replies")}</dt><dd>{profile?.stats.replies ?? 0}</dd></div>
                <div><dt>{t("profile.followers")}</dt><dd>{profile?.stats.followers ?? 0}</dd></div>
              </dl>

              <div className="profile-tabs" role="tablist" aria-label={t("profile.activity")} ref={tabsRef}>
                {(["posts", "replies", "saved"] as ProfileTab[]).map((item) => (
                  <button className={`profile-tab ${selectedTab === item ? "active" : ""}`} data-profile-tab={item} key={item} type="button" role="tab" aria-selected={selectedTab === item} onClick={() => switchTab(item)}>{t(profileTabKeys[item])}</button>
                ))}
                <span className={`filter-indicator ${indicator.ready ? "ready" : ""}`} style={{ width: indicator.width, transform: `translateX(${indicator.x}px)` }} aria-hidden="true" />
              </div>

              <div className={`profile-panel ${panelPhase}`} role="tabpanel">
                {loadingItems ? (
                  <Loading />
                ) : tabError ? (
                  <div className="empty-state content-fade">{tabError}</div>
                ) : tab === "replies" ? (
                  <div className="thread-list content-fade">
                    {replies.map((reply) => (
                      <a className="thread" href={`/d/${reply.discussionId}`} key={reply.id}>
                        <div className="thread-main">
                          <h3 className="thread-title">{reply.discussionTitle}</h3>
                          {reply.bodyMarkdown && <p className="thread-preview">{reply.bodyMarkdown}</p>}
                          <div className="meta"><span className="tag">{t("profile.replyTag")}</span></div>
                        </div>
                      </a>
                    ))}
                    {replies.length === 0 && <div className="empty-state">{t("profile.noReplies")}</div>}
                  </div>
                ) : (
                  <div className="thread-list content-fade">
                    {threads.map((thread) => <ThreadRow thread={thread} key={thread.id} showSender={tab === "saved"} />)}
                    {threads.length === 0 && <div className="empty-state">{tab === "saved" ? t("profile.noSaved") : t("profile.noDiscussions")}</div>}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>

        <aside className="profile-aside" aria-label={t("profile.details")}>
          <h2>{t("profile.about")}</h2>
          <dl>
            {profile && <div><dt>{t("profile.joined")}</dt><dd>{formatDateL(profile.joinedAt, locale)}</dd></div>}
            {profile && <div><dt>{t("profile.followingLabel")}</dt><dd>{profile.stats.following}</dd></div>}
            {profile && <div><dt>{t("profile.bio")}</dt><dd>{profile.bio || "—"}</dd></div>}
          </dl>
          {profile?.lastSeenAt && <p>{t("profile.lastSeen", { date: formatDateL(profile.lastSeenAt, locale) })}</p>}
        </aside>
      </main>
    </AppShell>
  );
}
