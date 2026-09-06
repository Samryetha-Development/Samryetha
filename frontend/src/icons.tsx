// 所有内联图标集中在这里，避免各页面重复定义。
// ViewIcon 依赖 discussion-app 里的 View 类型，暂时留在原文件。

export function SearchIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.7" /><path d="M16 16L21 21" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
}

export function ThreadIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 8v8M8 12h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>;
}

export function PlusIcon() {
  return <svg width="19" height="19" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
}

export function HamburgerIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 6.5h16M4 12h16M4 17.5h10" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
}

export function CloseIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
}

export function ProfileIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="8" r="3.5" stroke="currentColor" strokeWidth="1.6" /><path d="M5.5 19c1.6-3 3.9-4.5 6.5-4.5S16.9 16 18.5 19" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>;
}

export function MailIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.6" /><path d="M3.5 6.5 12 12l8.5-5.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

export function SettingsIcon() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" /><path d="M19 13.2v-2.4l-2-.7a7 7 0 0 0-.7-1.6l.9-1.9-1.8-1.8-1.9.9a7 7 0 0 0-1.6-.7l-.7-2H8.8l-.7 2a7 7 0 0 0-1.6.7l-1.9-.9-1.8 1.8.9 1.9a7 7 0 0 0-.7 1.6l-2 .7v2.4l2 .7a7 7 0 0 0 .7 1.6l-.9 1.9 1.8 1.8 1.9-.9a7 7 0 0 0 1.6.7l.7 2h2.4l.7-2a7 7 0 0 0 1.6-.7l1.9.9 1.8-1.8-.9-1.9a7 7 0 0 0 .7-1.6l2-.7Z" stroke="currentColor" strokeWidth="1.35" strokeLinejoin="round" /></svg>;
}

export function AdminIcon() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3l7 3v5c0 4.4-2.6 8.2-7 10-4.4-1.8-7-5.6-7-10V6l7-3Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>;
}

export function LogOutIcon() {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

export function EyeIcon({ visible }: { visible: boolean }) {
  return visible
    ? <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3.5 12s3.1-5 8.5-5 8.5 5 8.5 5-3.1 5-8.5 5-8.5-5-8.5-5Z" stroke="currentColor" strokeWidth="1.5" /><circle cx="12" cy="12" r="2.4" stroke="currentColor" strokeWidth="1.5" /></svg>
    : <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 4l16 16M10.2 7.2c.6-.1 1.2-.2 1.8-.2 5.4 0 8.5 5 8.5 5a13 13 0 0 1-2.1 2.6M14.6 16.6c-.8.3-1.7.4-2.6.4-5.4 0-8.5-5-8.5-5a13.8 13.8 0 0 1 3-3.3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><path d="M10.3 10.3a2.4 2.4 0 0 0 3.4 3.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>;
}
