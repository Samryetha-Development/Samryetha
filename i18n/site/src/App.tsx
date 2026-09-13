import { useEffect, useState, useCallback } from "react";
import type { User } from "./api";
import { getMe, logout } from "./api";
import CatalogPage from "./pages/CatalogPage";
import SubmitPage from "./pages/SubmitPage";
import MySubmissionsPage from "./pages/MySubmissionsPage";
import AdminPage from "./pages/AdminPage";
import LoginPage from "./pages/LoginPage";

type Tab = "catalog" | "submit" | "mine" | "admin" | "login";

function isAdmin(user: User) {
  return user.role === "admin" || user.role === "moderator";
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

  const handleLogout = async () => {
    await logout().catch(() => null);
    setUser(null);
    setTab("catalog");
  };

  const handleLogin = (u: User) => {
    setUser(u);
    setTab("catalog");
  };

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
              <button className="btn btn-ghost btn-sm" onClick={handleLogout}>
                Sign out
              </button>
            </div>
          ) : (
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setTab("login")}
            >
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
            <button
              className={`tab-btn${tab === "login" ? " active" : ""}`}
              onClick={() => setTab("login")}
            >
              Sign in
            </button>
          )}
        </div>

        {tab === "catalog" && <CatalogPage />}
        {tab === "submit" && user && (
          <SubmitPage user={user} onSubmitted={() => setTab("mine")} />
        )}
        {tab === "mine" && user && <MySubmissionsPage />}
        {tab === "admin" && adminUser && <AdminPage />}
        {tab === "login" && !user && <LoginPage onLogin={handleLogin} />}
      </main>
    </div>
  );
}
