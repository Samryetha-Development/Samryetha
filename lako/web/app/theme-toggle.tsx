"use client";

import { useEffect, useState } from "react";

function activeTheme(): "light" | "dark" {
  const saved = document.documentElement.dataset.theme;
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    setTheme(activeTheme());
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => { if (!document.documentElement.dataset.theme) setTheme(activeTheme()); };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  function toggle() {
    const next = activeTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("lako-theme", next);
    setTheme(next);
  }
  return <button className="theme-toggle" type="button" onClick={toggle} aria-label={theme === "dark" ? "Use light appearance" : "Use dark appearance"} title={theme === "dark" ? "Light appearance" : "Dark appearance"}><svg className="theme-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 15.2A8.5 8.5 0 0 1 8.8 4a8.5 8.5 0 1 0 11.2 11.2Z" /></svg><svg className="theme-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3.5" /><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42" /></svg></button>;
}
