"use client";

import { useEffect, useState } from "react";

/**
 * Nav links. "Log out" only shows once we know the visitor is signed in; it is a
 * top-level link to the API's RP-initiated logout endpoint (same-origin here), so
 * the SameSite=Lax session cookie is sent and the session is revoked.
 */
export function NavLinks() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/auth/me")
      .then((response) => { if (alive) setAuthed(response.ok); })
      .catch(() => { if (alive) setAuthed(false); });
    return () => { alive = false; };
  }, []);

  return (
    <nav>
      <a href="/account">Account</a>
      <a href="/account/security">Security</a>
      {authed === true && <a href="/oauth/end-session">Log out</a>}
      {authed === false && <a href="/login">Sign in</a>}
    </nav>
  );
}
