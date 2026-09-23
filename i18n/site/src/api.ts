/** API client for the i18n translation backend.
 *
 * 所有请求打到同域 /api/*，由 Vite 开发代理转发到 i18n 独立服务（:3002）。
 * 生产环境下翻译站与 i18n 服务同域部署，或通过 VITE_API_BASE 指定跨域地址。
 */

import { normalizeI18nLocale } from "./languages";

const BASE = import.meta.env.VITE_API_BASE ?? "";

// ---- response types from i18n service ----------------------------------

/** 与服务器 SubmissionOut 完全对应（字段名保持服务器命名） */
export interface Submission {
  id: number;
  locale: string;       // 服务器字段名 locale（不是 lang）
  key: string;
  value: string;
  note?: string | null;
  submitter_id: number;
  submitter_name: string;
  status: "pending" | "approved" | "rejected";
  reviewer_id?: number | null;
  reviewer_name?: string | null;
  review_note?: string | null;  // 服务器字段名 review_note（不是 reject_reason）
  reviewed_at?: number | null;
  created_at: number;   // 服务器字段名 created_at（不是 submitted_at）
  updated_at: number;
}

export interface SubmissionNote {
  id: number;
  submission_id: number;
  author_id: number;
  author_name: string;
  body: string;
  created_at: number;
}

/** 前端 catalog 条目：源字符串 + 可选目标语言翻译 */
export interface CatalogEntry {
  key: string;
  value: string;           // 英文源文本
  context?: string | null; // 对应服务器 description 字段
  translation?: string | null; // 目标语言译文（从目标 locale catalog 合并而来）
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

// 主站地址：登录/登出去主站操作（翻译站本身不建会话，只读共享 samryetha_session cookie）
export const MAIN_ORIGIN = (import.meta.env.VITE_MAIN_ORIGIN as string | undefined) ?? "http://localhost:3000";

/** 基于共享会话返回当前用户；未登录返回 null。 */
export async function getMe(): Promise<User | null> {
  try {
    const data = await req<{ user: User | null }>("/api/me");
    return data.user ?? null;
  } catch {
    return null;
  }
}

// ---- catalog ------------------------------------------------------------

interface ServerCatalogEntry {
  id: number;
  locale: string;
  key: string;
  value: string;
  description: string | null;
  created_at: number;
  updated_at: number;
}

interface ServerCatalogResponse {
  locale: string;
  entries: ServerCatalogEntry[];
  total: number;
}

/**
 * 取目录条目。
 * - 无 lang 参数：返回英文源字符串列表（供 SubmitPage 填充下拉框）
 * - 有 lang 参数：返回英文源 + 目标语言译文合并结果（供 CatalogPage 展示对照）
 */
export async function getCatalog(lang?: string): Promise<CatalogEntry[]> {
  const normalizedLang = lang ? normalizeI18nLocale(lang) : null;
  if (lang && !normalizedLang) throw new Error(`Unsupported locale: ${lang}`);
  // 英文源（始终需要）
  const srcRes = await req<ServerCatalogResponse>("/api/catalog/en");
  const srcMap: Record<string, ServerCatalogEntry> = {};
  for (const e of srcRes.entries) srcMap[e.key] = e;

  // 目标语言译文（按需）
  let tgtMap: Record<string, string> = {};
  if (normalizedLang) {
    const tgtRes = await req<ServerCatalogResponse>(`/api/catalog/${encodeURIComponent(normalizedLang)}`);
    for (const e of tgtRes.entries) tgtMap[e.key] = e.value;
  }

  return srcRes.entries.map((e) => ({
    key: e.key,
    value: e.value,
    context: e.description,
    translation: normalizedLang ? (tgtMap[e.key] ?? null) : undefined,
  }));
}

// ---- submissions --------------------------------------------------------

export interface ListSubmissionsParams {
  locale?: string;
  key?: string;
  status?: "pending" | "approved" | "rejected";
  limit?: number;
  offset?: number;
}

export async function listSubmissions(
  params: ListSubmissionsParams = {},
): Promise<Submission[]> {
  const q = new URLSearchParams();
  if (params.locale) q.set("locale", params.locale);
  if (params.key) q.set("key", params.key);
  if (params.status) q.set("status", params.status);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  const res = await req<{ submissions: Submission[]; total: number }>(
    `/api/submissions${qs ? "?" + qs : ""}`,
  );
  return res.submissions;
}

export interface SubmitBody {
  key: string;
  locale: string;  // 服务器期望 locale（不是 lang）
  value: string;
  note?: string;
}

export async function submitTranslation(body: SubmitBody): Promise<Submission> {
  const locale = normalizeI18nLocale(body.locale);
  if (!locale || locale === "en") throw new Error(`Unsupported target locale: ${body.locale}`);
  return req<Submission>("/api/submissions", {
    method: "POST",
    body: JSON.stringify({ ...body, locale }),
  });
}

export async function approveSubmission(id: number, note?: string): Promise<Submission> {
  return req<Submission>(`/api/submissions/${id}/review`, {
    method: "POST",
    body: JSON.stringify({ action: "approve", note: note || null }),
  });
}

export async function rejectSubmission(
  id: number,
  reason?: string,
): Promise<Submission> {
  return req<Submission>(`/api/submissions/${id}/review`, {
    method: "POST",
    body: JSON.stringify({ action: "reject", note: reason ?? null }),
  });
}

export async function listSubmissionNotes(id: number): Promise<SubmissionNote[]> {
  const result = await req<{ notes: SubmissionNote[] }>(`/api/submissions/${id}/notes`);
  return result.notes;
}

export async function addSubmissionNote(id: number, body: string): Promise<SubmissionNote> {
  return req<SubmissionNote>(`/api/submissions/${id}/notes`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}
