export type TaskPriority = "urgent" | "normal";
export type TaskStatus = "open" | "done";

export type TaskItem = {
  id: number;
  author: { id: number; handle: string; displayName: string };
  category: string;
  title: string;
  notes: string;
  priority: TaskPriority;
  status: TaskStatus;
  doneAt: number | null;
  createdAt: number;
  updatedAt: number;
};

export type TaskList = {
  items: TaskItem[];
  categories: { category: string; open: number; done: number }[];
  canWrite: boolean;
};

const apiOrigin = (import.meta.env.VITE_API_ORIGIN || "").replace(/\/$/, "");
export const forumOrigin = (import.meta.env.VITE_FORUM_ORIGIN || "http://localhost:3000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiOrigin}${path}`, {
    ...init,
    credentials: "include",
    headers: init.body ? { "content-type": "application/json", ...init.headers } : init.headers,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { message?: string } | null;
    throw new ApiError(response.status, payload?.message || `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  list: () => request<TaskList>("/api/tasks"),
  me: () => request<{ user: { id: number } }>("/api/auth/me"),
  create: (body: object) => request<TaskItem>("/api/tasks", { method: "POST", body: JSON.stringify(body) }),
  update: (id: number, body: object) => request<TaskItem>(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  setStatus: (id: number, status: TaskStatus) => request<TaskItem>(`/api/tasks/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }),
  delete: (id: number) => request<{ ok: boolean }>(`/api/tasks/${id}`, { method: "DELETE" }),
};
