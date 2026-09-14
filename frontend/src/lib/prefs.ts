// 用户偏好工具：reduce_motion 优先读用户偏好，缺省回退系统 prefers-reduced-motion。
// Preference helpers: reduce_motion prefers the user setting, falling back to the system.
import type { UserDTO } from "./api";

// 是否减少动画：用户显式设置（true/false）优先；未设置回退系统偏好。
// Whether to reduce motion: an explicit user setting (true/false) wins;
// when unset, fall back to the system preference.
export function reducedMotion(user?: UserDTO | null): boolean {
  const pref = user?.settings?.reduce_motion;
  if (pref === true) return true;
  if (pref === false) return false;
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
