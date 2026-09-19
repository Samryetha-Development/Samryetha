import { useEffect, useState, useCallback } from "react";
import type { User } from "./api";
import { getMe, MAIN_ORIGIN } from "./api";
import CatalogPage from "./pages/CatalogPage";
import SubmitPage from "./pages/SubmitPage";
import MySubmissionsPage from "./pages/MySubmissionsPage";
import AdminPage from "./pages/AdminPage";

type Tab = "catalog" | "submit" | "mine" | "admin";

function isAdmin(user: User) {
  return user.role === "admin" || user.role === "moderator";
}

// 登录入口统一走 IdP：不再跳主站那张登录页，直接从主站的 OIDC 入口起跳，
// 签完带着会话回到翻译站自己。主站只放行 SIGNIN_RETURN_ORIGINS 白名单里的
// origin，本机开发要记得把 http://localhost:5200 加进去。
function handleSignIn() {
  const returnTo = encodeURIComponent(window.location.origin);
  window.location.assign(`${MAIN_ORIGIN}/api/auth/login?returnTo=${returnTo}`);
}

function handleSignOut() {
  // 翻译站不持有会话：走主站登出端点清掉 samryetha_session（并顺带结束 IdP 会话）。
  // 带上 returnTo，否则会被甩到主站首页而不是回翻译站。
  const returnTo = encodeURIComponent(window.location.origin);
  window.location.assign(`${MAIN_ORIGIN}/api/auth/oidc/logout?returnTo=${returnTo}`);
}

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined); // undefined = loading
  const [tab, setTab] = useState<Tab>("catalog");

  const refreshUser = useCallback(async () => {
    const u = await getMe();
    setUser(u);
  }, []);

  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  if (user === undefined) {
    return (
      <div className="site-wrap">
        <div className="empty-state">Loading…</div>
      </div>
    );
  }

  const adminUser = user && isAdmin(user);

  return (
    <div className="site-wrap">
      <header className="site-header">
        <div className="site-header-inner">
          <a
            href="#"
            className="site-logo"
            onClick={(e) => { e.preventDefault(); setTab("catalog"); }}
          >
            Samryetha<span>Translations</span>
          </a>
          <div className="header-spacer" />
          {user ? (
            <div className="row">
              <span className="text-muted">{user.display_name}</span>
              <button className="btn btn-ghost btn-sm" onClick={handleSignOut}>
                Sign out
              </button>
            </div>
          ) : (
            <button className="btn btn-secondary btn-sm" onClick={handleSignIn}>
              Sign in
            </button>
          )}
        </div>
      </header>

      <main className="site-main">
        <div className="tab-bar">
          <button
            className={`tab-btn${tab === "catalog" ? " active" : ""}`}
            onClick={() => setTab("catalog")}
          >
            Source Strings
          </button>
          {user && (
            <>
              <button
                className={`tab-btn${tab === "submit" ? " active" : ""}`}
                onClick={() => setTab("submit")}
              >
                Submit Translation
              </button>
              <button
                className={`tab-btn${tab === "mine" ? " active" : ""}`}
                onClick={() => setTab("mine")}
              >
                My Submissions
              </button>
            </>
          )}
          {adminUser && (
            <button
              className={`tab-btn${tab === "admin" ? " active" : ""}`}
              onClick={() => setTab("admin")}
            >
              Admin Review
            </button>
          )}
          {!user && (
            <p className="tab-hint">
              Already have an account?{" "}
              <a href={`${MAIN_ORIGIN}/login`} onClick={(e) => { e.preventDefault(); handleSignIn(); }}>
                Sign in on Samryetha
              </a>{" "}
              to submit translations.
            </p>
          )}
        </div>

        {tab === "catalog" && <CatalogPage />}
        {tab === "submit" && user && (
          <SubmitPage user={user} onSubmitted={() => setTab("mine")} />
        )}
        {tab === "mine" && user && <MySubmissionsPage />}
        {tab === "admin" && adminUser && <AdminPage />}
      </main>
    </div>
  );
}
