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

function handleSignIn() {
  window.location.assign(`${MAIN_ORIGIN}/login`);
}

function handleSignOut() {
  // 翻译站不持有会话；登出跳主站（主站清除 samryetha_session 后回来刷新即登出态）
  window.location.assign(MAIN_ORIGIN);
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
