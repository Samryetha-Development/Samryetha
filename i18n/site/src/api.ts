/** API client for the i18n translation backend. */

const BASE = import.meta.env.VITE_API_BASE ?? "";

export interface CatalogEntry {
  key: string;
  source_lang: string;
  value: string;
  context?: string | null;
  translation?: string | null;
}

export interface Submission {
  id: number;
  key: string;
  lang: string;
  value: string;
  note?: string | null;
  status: "pending" | "approved" | "rejected";
  reject_reason?: string | null;
  user_id: number;
  reviewer_id?: number | null;
  submitted_at: number;
  reviewed_at?: number | null;
  username?: string | null;
  display_name?: string | null;
}

export interface User {
  id: number;
  username: string;
  display_name: string;
  email: string;
  role: string;
}

export interface ApiError {
  error: { code: string; message: string; requestId?: string };
}

async function req<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: { message: res.statusText } }));
    const msg = (body as ApiError).error?.message ?? res.statusText;
    throw new Error(msg);
  }
  // 204 / no-body
  const ct = res.headers.get("content-type") ?? "";
  if (res.status === 204 || !ct.includes("application/json")) return undefined as T;
  return res.json() as Promise<T>;
}

// ---- auth ---------------------------------------------------------------

export async function getMe(): Promise<User | null> {
  try {
    return await req<User>("/api/users/me");
  } catch {
    return null;
  }
}

export interface LoginBody { email: string; password: string }
export async function login(body: LoginBody): Promise<User> {
  return req<User>("/api/auth/login", { method: "POST", body: JSON.stringify(body) });
}

export async function logout(): Promise<void> {
  await req<void>("/api/auth/logout", { method: "POST", body: "{}" });
}

// ---- catalog ------------------------------------------------------------

export async function getCatalog(lang?: string): Promise<CatalogEntry[]> {
  const q = lang ? `?lang=${encodeURIComponent(lang)}` : "";
  const res = await req<{ entries: CatalogEntry[] }>(`/api/i18n/catalog${q}`);
  return res.entries;
}

// ---- submissions --------------------------------------------------------

export interface ListSubmissionsParams {
  lang?: string;
  key?: string;
  status?: "pending" | "approved" | "rejected";
  limit?: number;
  offset?: number;
}

export async function listSubmissions(
  params: ListSubmissionsParams = {},
): Promise<Submission[]> {
  const q = new URLSearchParams();
  if (params.lang) q.set("lang", params.lang);
  if (params.key) q.set("key", params.key);
  if (params.status) q.set("status", params.status);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await req<{ submissions: Submission[] }>(
    `/api/i18n/submissions${qs ? "?" + qs : ""}`,
  );
  return res.submissions;
}

export interface SubmitBody {
  key: string;
  lang: string;
  value: string;
  note?: string;
}

export async function submitTranslation(body: SubmitBody): Promise<Submission> {
  return req<Submission>("/api/i18n/submissions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function approveSubmission(id: number): Promise<Submission> {
  return req<Submission>(`/api/i18n/submissions/${id}/approve`, { method: "POST", body: "{}" });
}

export async function rejectSubmission(
  id: number,
  reason?: string,
): Promise<Submission> {
  return req<Submission>(`/api/i18n/submissions/${id}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}
