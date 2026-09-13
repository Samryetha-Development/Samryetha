"use client";

import { useEffect, useState } from "react";

export default function Verify() {
  const [state, setState] = useState<"working" | "done" | "error">("working");
  useEffect(() => {
    const token = new URLSearchParams(location.search).get("token");
    if (!token) { setState("error"); return; }
    fetch("/api/account/email/verify/confirm", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
    }).then((response) => setState(response.ok ? "done" : "error")).catch(() => setState("error"));
  }, []);
  return <main><section className="auth-wrap"><div className="panel"><div className="eyebrow">Lako</div><h1>Email verification</h1>
    {state === "working" && <p className="subtle">Confirming…</p>}
    {state === "done" && <p className="subtle">Address verified. <a className="auth-link" href="/login">Back to sign in</a></p>}
    {state === "error" && <p className="subtle">This link is invalid or has expired. Request a new one from account security.</p>}
  </div></section></main>;
}
