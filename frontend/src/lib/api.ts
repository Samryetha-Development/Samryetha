// 薄 API client：同源 fetch（走 Vite dev proxy / Express 生产 proxy → 后端 3001），
// 统一解析后端错误模型 `{ error: { code, message, requestId, details? } }`。

export type AuthorRef = { id: number; username: string; handle: string; displayName: string };
export type BoardRef = { id: number; slug: string; name: string };
export type UserRole = "student" | "admin";
export type UserStatus = "pending" | "active" | "banned" | "deactivated";
export type BodyFormat = "markdown" | "text";

export type ThreadSummary = {
  id: number;
  title: string;
  preview: string;
  board: BoardRef;
  author: AuthorRef;
  replyCount: number;
  isPinned: boolean;
  isLocked: boolean;
  createdAt: number;
  lastActivityAt: number;
};

export type DiscussionDetail = ThreadSummary & {
  bodyMarkdown: string;
  bodyHtml: string | null;
  bodyFormat: BodyFormat;
  saveCount: number;
  isSaved: boolean;
  isFollowing: boolean;
  can: { update: boolean; delete: boolean };
};

export type ReplyDTO = {
  id: number;
  discussionId: number;
  parentReplyId: number | null;
  author: AuthorRef;
  bodyMarkdown: string;
  bodyHtml: string | null;
  bodyFormat: BodyFormat;
  isDeleted: boolean;
  createdAt: number;
  updatedAt: number;
};

export type ReplyFeedItem = ReplyDTO & { discussionTitle: string };

export type BoardVisibility = "public" | "members" | "private";
export type PostingPolicy = "everyone" | "members" | "moderators";

export type BoardSummary = {
  id: number;
  slug: string;
  name: string;
  description: string;
  visibility: BoardVisibility;
  postingPolicy: PostingPolicy;
  memberCount: number;
  todayActivity: number;
  currentUserRole: "member" | "moderator" | null;
};

export type UserDTO = {
  id: number;
  username: string;
  handle: string;
  displayName: string;
  email: string;
  recoveryEmail: string | null;
  role: UserRole;
  status: UserStatus;
  bio: string;
  emailVerified: boolean;
  avatarObjectKey: string | null;
  settings: Record<string, unknown>;
  createdAt: number;
  lastSeenAt: number | null;
};

export type PublicProfile = {
  id: number;
  username: string;
  handle: string;
  displayName: string;
  bio: string;
  avatarObjectKey: string | null;
  joinedAt: number;
  lastSeenAt: number | null;
  stats: { discussions: number; replies: number; followers: number; following: number };
  isFollowing: boolean;
};

export type NotificationDTO = {
  id: number;
  type: string;
  actor: AuthorRef | null;
  body: string | null;
  discussionId: number | null;
  replyId: number | null;
  isRead: boolean;
  createdAt: number;
};

export type ConversationSummary = {
  id: number;
  otherUser: AuthorRef;
  lastMessage: { body: string; senderId: number; createdAt: number } | null;
  unreadCount: number;
  lastMessageAt: number;
};

export type DirectMessage = {
  id: number;
  senderId: number;
  body: string;
  source: string;
  isRead: boolean;
  createdAt: number;
};

export type Presence = { onlineCount: number; onlineUsers: AuthorRef[] };
export type FeedPage<T> = { items: T[]; nextCursor: string | null };
export type SearchResult = { items: ThreadSummary[]; total: number };

export type AdminUser = {
  id: number;
  username: string;
  handle: string;
  displayName: string;
  email: string;
  role: UserRole;
  status: UserStatus;
  emailVerified: boolean;
  createdAt: number;
  lastSeenAt: number | null;
  banActive: boolean;
  reportCount: number;
};

export type AdminStats = {
  users: { total: number; pending: number; active: number; banned: number; deactivated: number };
  content: { discussions: number; replies: number; boards: number };
  moderation: { openReports: number; activeBans: number };
  activity: {
    activeToday: number;
    newUsersToday: number;
    newDiscussionsToday: number;
    newRepliesToday: number;
    onlineNow: number;
  };
};

export type ReportTarget = {
  type: "discussion" | "reply" | "user";
  id: number;
  title?: string;
  boardSlug?: string;
  username?: string;
  handle?: string;
  displayName?: string;
  discussionId?: number;
};

export type ReportDTO = {
  id: number;
  reporter: AuthorRef;
  reportableType: string;
  reportableId: number;
  target?: ReportTarget;
  reason: string | null;
  status: "open" | "in_progress" | "resolved" | "dismissed";
  createdAt: number;
};

export type ModerationAction = {
  id: number;
  actor: AuthorRef;
  action: string;
  targetType: string;
  targetId: number;
  reason: string | null;
  createdAt: number;
};

export type DeletedDiscussion = {
  id: number;
  boardSlug: string;
  title: string;
  preview: string;
  deletedBy: AuthorRef | null;
  deletedAt: number;
  reason: string | null;
};

export type DeletedReply = {
  id: number;
  discussionId: number;
  discussionTitle: string;
  preview: string;
  deletedBy: AuthorRef | null;
  deletedAt: number;
  reason: string | null;
};

export type BoardMember = { id: number; username: string; handle: string; displayName: string; role: "member" | "moderator" };

export type FeedbackType = "bug" | "suggestion";
export type FeedbackUrgency = "urgent" | "normal";
export type FeedbackStatus = "open" | "done" | "expired";

export type FeedbackItem = {
  id: number;
  seq: number;
  projectId: number;
  author: AuthorRef;
  title: string;
  detail: string;
  type: FeedbackType;
  urgency: FeedbackUrgency;
  status: FeedbackStatus;
  closedAt: number | null;
  editedAt: number | null;
  createdAt: number;
  updatedAt: number;
};

export type FeedbackComment = {
  id: number;
  itemId: number;
  parentCommentId: number | null;
  author: AuthorRef;
  body: string;
  isDeleted: boolean;
  createdAt: number;
  updatedAt: number;
};

export type FeedbackProjectSummary = {
  id: number;
  name: string;
  description: string;
  memberCount: number;
  isProgrammer: boolean;
  createdAt: number;
};

export type FeedbackProjectMember = {
  userId: number;
  username: string;
  handle: string;
  displayName: string;
  isProgrammer: boolean;
  joinedAt: number;
};

export type FeedbackProjectAdmin = {
  id: number;
  name: string;
  description: string;
  members: FeedbackProjectMember[];
  createdAt: number;
};

export type FeedbackApiKey = {
  id: number;
  name: string;
  prefix: string;
  role: "read" | "write";
  projectIds: number[];
  enabled: boolean;
  lastUsedAt: number | null;
  createdAt: number;
};

export type FeedbackBackupInfo = { name: string; size: number; createdAt: number };
export type FeedbackBackupSettings = { backupCron: string; backupKeep: number };

export type TaskPriority = "urgent" | "normal";
export type TaskStatus = "open" | "done";

export type TaskItem = {
  id: number;
  author: AuthorRef;
  category: string;
  title: string;
  notes: string;
  priority: TaskPriority;
  status: TaskStatus;
  doneAt: number | null;
  createdAt: number;
  updatedAt: number;
};

export type TaskCategoryCount = { category: string; open: number; done: number };

export type TaskList = { items: TaskItem[]; categories: TaskCategoryCount[]; canWrite: boolean };

export type ApiErrorPayload = { code: string; message: string; requestId?: string; details?: unknown };

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message);
    this.code = payload.code;
    this.status = status;
    this.details = payload.details;
  }
}

async function apiFetch<T>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? "GET",
      headers: opts.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) throw new ApiError(0, { code: "TIMEOUT", message: "Request timed out" });
    throw error;
  } finally {
    clearTimeout(timeout);
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    // 非 JSON 响应：交给错误分支
  }
  if (!res.ok) {
    const payload = (data as { error?: ApiErrorPayload })?.error;
    throw new ApiError(res.status, payload ?? { code: "UNKNOWN", message: `Request failed (${res.status})` });
  }
  return data as T;
}

const qs = (params: Record<string, string | number | undefined>) => {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const api = {
  auth: {
    me: () => apiFetch<{ user: UserDTO }>("/api/auth/me"),
    login: (body: { username: string; password: string }) =>
      apiFetch<{ user: UserDTO; sessionExpiresAt: number }>("/api/auth/login", { method: "POST", body }),
    logout: () => apiFetch<void>("/api/auth/logout", { method: "POST" }),
    register: (body: { username: string; password: string }) =>
      apiFetch<{ userId: number; message: string }>("/api/auth/register", { method: "POST", body }),
    changePassword: (body: { currentPassword: string; newPassword: string }) =>
      apiFetch<{ ok: boolean }>("/api/auth/change-password", { method: "POST", body }),
    forgotPassword: (body: { username: string; recoveryEmail: string }) =>
      apiFetch<{ ok: boolean; message: string }>("/api/auth/forgot-password", { method: "POST", body }),
    resetPassword: (body: { token: string; newPassword: string }) =>
      apiFetch<{ ok: boolean }>("/api/auth/reset-password", { method: "POST", body }),
  },

  users: {
    get: (username: string) => apiFetch<PublicProfile>(`/api/users/${encodeURIComponent(username)}`),
    posts: (username: string, cursor?: string) =>
      apiFetch<FeedPage<ThreadSummary>>(`/api/users/${encodeURIComponent(username)}/posts${qs({ cursor })}`),
    replies: (username: string, cursor?: string) =>
      apiFetch<FeedPage<ReplyFeedItem>>(`/api/users/${encodeURIComponent(username)}/replies${qs({ cursor })}`),
    saved: (username: string, cursor?: string) =>
      apiFetch<FeedPage<ThreadSummary>>(`/api/users/${encodeURIComponent(username)}/saved${qs({ cursor })}`),
    follow: (username: string) => apiFetch<void>(`/api/users/${encodeURIComponent(username)}/follow`, { method: "POST" }),
    unfollow: (username: string) => apiFetch<void>(`/api/users/${encodeURIComponent(username)}/follow`, { method: "DELETE" }),
    updateProfile: (patch: { displayName?: string; username?: string; recoveryEmail?: string; bio?: string; settings?: Record<string, boolean> }) =>
      apiFetch<{ user: UserDTO }>("/api/me/profile", { method: "PATCH", body: patch }),
  },

  boards: {
    list: () => apiFetch<{ items: BoardSummary[] }>("/api/boards"),
    get: (slug: string) => apiFetch<BoardSummary>(`/api/boards/${encodeURIComponent(slug)}`),
    create: (body: { name: string; slug: string; description?: string; visibility?: string; postingPolicy?: string }) =>
      apiFetch<BoardSummary>("/api/boards", { method: "POST", body }),
    update: (slug: string, body: { name?: string; description?: string; visibility?: string; postingPolicy?: string }) =>
      apiFetch<BoardSummary>(`/api/boards/${encodeURIComponent(slug)}`, { method: "PATCH", body }),
    del: (slug: string, body: { reason?: string } = {}) =>
      apiFetch<void>(`/api/boards/${encodeURIComponent(slug)}`, { method: "DELETE", body }),
    members: (slug: string) =>
      apiFetch<{ items: BoardMember[] }>(`/api/boards/${encodeURIComponent(slug)}/members`),
    updateMemberRole: (slug: string, userId: number, body: { role: "member" | "moderator" }) =>
      apiFetch<void>(`/api/boards/${encodeURIComponent(slug)}/members/${userId}`, { method: "PATCH", body }),
  },

  discussions: {
    feed: (opts: { feed?: "latest" | "followed"; board?: string; cursor?: string; limit?: number }) =>
      apiFetch<FeedPage<ThreadSummary>>(`/api/discussions${qs(opts)}`),
    boardFeed: (slug: string, cursor?: string) =>
      apiFetch<FeedPage<ThreadSummary>>(`/api/boards/${encodeURIComponent(slug)}/discussions${qs({ cursor })}`),
    get: (id: number) => apiFetch<DiscussionDetail>(`/api/discussions/${id}`),
    create: (body: { boardSlug: string; title: string; bodyMarkdown: string; bodyFormat?: BodyFormat }) =>
      apiFetch<DiscussionDetail>("/api/discussions", { method: "POST", body }),
    update: (id: number, body: { title?: string; bodyMarkdown?: string; bodyFormat?: BodyFormat }) =>
      apiFetch<DiscussionDetail>(`/api/discussions/${id}`, { method: "PATCH", body }),
    del: (id: number) => apiFetch<void>(`/api/discussions/${id}`, { method: "DELETE", body: {} }),
    save: (id: number) => apiFetch<void>(`/api/discussions/${id}/save`, { method: "POST" }),
    unsave: (id: number) => apiFetch<void>(`/api/discussions/${id}/save`, { method: "DELETE" }),
    follow: (id: number) => apiFetch<void>(`/api/discussions/${id}/follow`, { method: "POST" }),
    unfollow: (id: number) => apiFetch<void>(`/api/discussions/${id}/follow`, { method: "DELETE" }),
    pin: (id: number) => apiFetch<void>(`/api/discussions/${id}/pin`, { method: "POST" }),
    lock: (id: number) => apiFetch<void>(`/api/discussions/${id}/lock`, { method: "POST" }),
    replies: (id: number) => apiFetch<{ items: ReplyDTO[] }>(`/api/discussions/${id}/replies`),
    createReply: (id: number, body: { bodyMarkdown: string; bodyFormat?: BodyFormat; parentReplyId?: number | null }) =>
      apiFetch<ReplyDTO>(`/api/discussions/${id}/replies`, { method: "POST", body }),
    updateReply: (id: number, body: { bodyMarkdown: string; bodyFormat?: BodyFormat }) => apiFetch<ReplyDTO>(`/api/replies/${id}`, { method: "PATCH", body }),
    delReply: (id: number) => apiFetch<void>(`/api/replies/${id}`, { method: "DELETE", body: {} }),
  },

  notifications: {
    list: (cursor?: string) =>
      apiFetch<{ items: NotificationDTO[]; unreadCount: number; nextCursor: string | null }>(`/api/notifications${qs({ cursor })}`),
    unreadCount: () => apiFetch<{ unreadCount: number }>("/api/notifications/unread-count"),
    markRead: (id: number) => apiFetch<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: "POST" }),
    markAllRead: () => apiFetch<{ ok: boolean }>("/api/notifications/read-all", { method: "POST" }),
  },

  messages: {
    conversations: () => apiFetch<{ items: ConversationSummary[] }>("/api/messages/conversations"),
    list: (id: number) => apiFetch<{ items: DirectMessage[]; otherUser: AuthorRef }>(`/api/messages/conversations/${id}`),
    send: (body: { username: string; body: string }) => apiFetch<{ conversationId: number }>("/api/messages", { method: "POST", body }),
    markRead: (id: number) => apiFetch<{ ok: boolean }>(`/api/messages/conversations/${id}/read`, { method: "POST" }),
    unreadCount: () => apiFetch<{ unreadCount: number }>("/api/messages/unread-count"),
  },

  admin: {
    stats: () => apiFetch<AdminStats>("/api/admin/stats"),
    users: (params: { q?: string; status?: UserStatus; role?: UserRole; cursor?: number; limit?: number } = {}) =>
      apiFetch<FeedPage<AdminUser>>(`/api/admin/users${qs(params)}`),
    changeRole: (id: number, body: { role: UserRole; reason?: string }) =>
      apiFetch<AdminUser>(`/api/admin/users/${id}/role`, { method: "PATCH", body }),
    changeStatus: (id: number, body: { status: "active" | "deactivated"; reason?: string }) =>
      apiFetch<AdminUser>(`/api/admin/users/${id}/status`, { method: "PATCH", body }),
    verifyUser: (id: number) => apiFetch<AdminUser>(`/api/admin/users/${id}/verify`, { method: "POST", body: {} }),
    resetPassword: (id: number) => apiFetch<{ temporaryPassword: string }>(`/api/admin/users/${id}/reset-password`, { method: "POST", body: {} }),
    deleteUser: (id: number) => apiFetch<{ ok: boolean }>(`/api/admin/users/${id}`, { method: "DELETE" }),
    deletedContent: (params: { discussionCursor?: number; replyCursor?: number; limit?: number } = {}) =>
      apiFetch<{ discussions: DeletedDiscussion[]; replies: DeletedReply[]; nextDiscussionCursor: number | null; nextReplyCursor: number | null }>(
        `/api/admin/moderation/deleted${qs(params)}`,
      ),
  },

  moderation: {
    reports: (params: { status?: string; cursor?: number; limit?: number } = {}) =>
      apiFetch<FeedPage<ReportDTO>>(`/api/moderation/reports${qs(params)}`),
    resolveReport: (id: number, body: { status: string; action?: string; reason?: string }) =>
      apiFetch<ReportDTO>(`/api/moderation/reports/${id}`, { method: "PATCH", body }),
    ban: (body: { username: string; reason?: string; durationHours?: number }) =>
      apiFetch<void>("/api/moderation/bans", { method: "POST", body }),
    unban: (username: string, body: { reason?: string } = {}) =>
      apiFetch<void>(`/api/moderation/bans/${encodeURIComponent(username)}`, { method: "DELETE", body }),
    actions: (params: { cursor?: number; limit?: number } = {}) =>
      apiFetch<FeedPage<ModerationAction>>(`/api/moderation/actions${qs(params)}`),
    restore: (body: { targetType: "discussion" | "reply"; targetId: number; reason?: string }) =>
      apiFetch<void>("/api/moderation/restore", { method: "POST", body }),
  },

  search: (q: string) => apiFetch<SearchResult>(`/api/search?q=${encodeURIComponent(q)}`),

  presence: {
    heartbeat: () => apiFetch<{ onlineCount: number }>("/api/presence/heartbeat", { method: "POST" }),
    get: () => apiFetch<Presence>("/api/presence"),
  },

  feedback: {
    myProjects: () => apiFetch<{ items: FeedbackProjectSummary[] }>("/api/feedback/projects/mine"),
    list: (projectId: number) => apiFetch<{ items: FeedbackItem[]; canManage: boolean }>(`/api/feedback?projectId=${projectId}`),
    create: (body: { projectId: number; title: string; detail?: string; type: FeedbackType; urgency?: FeedbackUrgency }) =>
      apiFetch<FeedbackItem>("/api/feedback", { method: "POST", body }),
    update: (id: number, body: { title?: string; detail?: string; type?: FeedbackType; urgency?: FeedbackUrgency }) =>
      apiFetch<FeedbackItem>(`/api/feedback/${id}`, { method: "PATCH", body }),
    del: (id: number) => apiFetch<void>(`/api/feedback/${id}`, { method: "DELETE", body: {} }),
    setStatus: (id: number, status: FeedbackStatus) =>
      apiFetch<FeedbackItem>(`/api/feedback/${id}/status`, { method: "POST", body: { status } }),
    comments: (id: number) => apiFetch<{ items: FeedbackComment[] }>(`/api/feedback/${id}/comments`),
    createComment: (id: number, body: { body: string; parentCommentId?: number | null }) =>
      apiFetch<FeedbackComment>(`/api/feedback/${id}/comments`, { method: "POST", body }),
    updateComment: (id: number, body: { body: string }) =>
      apiFetch<FeedbackComment>(`/api/feedback/comments/${id}`, { method: "PATCH", body }),
    delComment: (id: number) => apiFetch<void>(`/api/feedback/comments/${id}`, { method: "DELETE", body: {} }),
  },

  feedbackAdmin: {
    projects: () => apiFetch<{ items: FeedbackProjectAdmin[] }>("/api/feedback/projects"),
    createProject: (body: { name: string; description?: string }) =>
      apiFetch<FeedbackProjectAdmin>("/api/feedback/projects", { method: "POST", body }),
    updateProject: (id: number, body: { name?: string; description?: string }) =>
      apiFetch<void>(`/api/feedback/projects/${id}`, { method: "PATCH", body }),
    delProject: (id: number) => apiFetch<void>(`/api/feedback/projects/${id}`, { method: "DELETE", body: {} }),
    setMembers: (id: number, members: { userId: number; isProgrammer: boolean }[]) =>
      apiFetch<void>(`/api/feedback/projects/${id}/members`, { method: "PUT", body: { members } }),
    keys: () => apiFetch<{ items: FeedbackApiKey[] }>("/api/admin/feedback/keys"),
    createKey: (body: { name: string; role: "read" | "write"; projectIds: number[] }) =>
      apiFetch<{ key: string; keyRow: FeedbackApiKey }>("/api/admin/feedback/keys", { method: "POST", body }),
    setKeyEnabled: (id: number, enabled: boolean) =>
      apiFetch<void>(`/api/admin/feedback/keys/${id}`, { method: "PUT", body: { enabled } }),
    delKey: (id: number) => apiFetch<void>(`/api/admin/feedback/keys/${id}`, { method: "DELETE", body: {} }),
    backups: () => apiFetch<{ backups: FeedbackBackupInfo[]; settings: FeedbackBackupSettings }>("/api/admin/feedback/backups"),
    createBackup: () => apiFetch<{ backup: FeedbackBackupInfo }>("/api/admin/feedback/backups/create", { method: "POST", body: {} }),
    restoreBackup: (name: string) =>
      apiFetch<{ ok: boolean; restartRequired: boolean }>("/api/admin/feedback/backups/restore", { method: "POST", body: { name } }),
    saveBackupSettings: (body: FeedbackBackupSettings) =>
      apiFetch<void>("/api/admin/feedback/backups/settings", { method: "PUT", body }),
  },

  tasks: {
    list: () => apiFetch<TaskList>("/api/tasks"),
    create: (body: { category?: string; title: string; notes?: string; priority?: TaskPriority; status?: TaskStatus }) =>
      apiFetch<TaskItem>("/api/tasks", { method: "POST", body }),
    update: (id: number, body: { category?: string; title?: string; notes?: string; priority?: TaskPriority }) =>
      apiFetch<TaskItem>(`/api/tasks/${id}`, { method: "PATCH", body }),
    del: (id: number) => apiFetch<void>(`/api/tasks/${id}`, { method: "DELETE", body: {} }),
    setStatus: (id: number, status: TaskStatus) =>
      apiFetch<TaskItem>(`/api/tasks/${id}/status`, { method: "POST", body: { status } }),
  },
};
