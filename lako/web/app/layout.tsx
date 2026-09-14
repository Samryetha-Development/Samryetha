import type { Metadata } from "next";
import { ThemeToggle } from "./theme-toggle";
import "./styles.css";

export const metadata: Metadata = { title: "Lako", description: "Identity, quietly handled." };

const themeScript = `(function(){try{var t=localStorage.getItem("lako-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(_){}})()`;

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head><body><header className="nav"><a href="/" className="brand">Lako</a><div className="nav-actions"><nav><a href="/account">Account</a><a href="/account/security">Security</a></nav><ThemeToggle /></div></header>{children}<footer>Self-hosted identity infrastructure.</footer></body></html>;
}
