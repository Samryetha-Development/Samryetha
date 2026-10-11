// 薄 API client：同源 fetch（走 Vite dev proxy / Express 生产 proxy → 后端 3001），
// 统一解析后端错误模型 `{ error: { code, message, requestId, details? } }`。

import type { components } from "./generated/openapi";

export type AuthorRef = { id: number; username: string; handle: string; displayName: string };
export type BoardRef = { id: number; slug: string; name: string };
export type UserRole = components["schemas"]["AccountRole"];
export type UserStatus = components["schemas"]["AccountStatus"];
export type BodyFormat = components["schemas"]["BodyFormat"];
export type MainpageSort = "date" | "replies";

export type ThreadSummary = components["schemas"]["ThreadSummaryResponse"];

export type AttachmentRef = components["schemas"]["DiscussionAttachmentResponse"];

export type DraftInput = components["schemas"]["SaveDraftBody"];
export type DraftSummary = components["schemas"]["DraftSummaryResponse"];
export type DraftDetail = components["schemas"]["DraftDetailResponse"];

export type PollResponse = components["schemas"]["PollResponse"];

export type DiscussionDetail = components["schemas"]["DiscussionDetailResponse"];

export type ReplyDTO = components["schemas"]["ReplyResponse"];

export type ReplyFeedItem = ReplyDTO & { discussionTitle: string };

export type BoardVisibility = components["schemas"]["BoardVisibility"];
export type PostingPolicy = components["schemas"]["PostingPolicy"];
export type BoardSummary = components["schemas"]["BoardSummaryResponse"];
export type UserDTO = components["schemas"]["UserResponse"];
export type PublicProfile = components["schemas"]["PublicProfileResponse"];

export type NotificationDTO = components["schemas"]["NotificationResponse"];
export type NotificationListResponse = components["schemas"]["NotificationListResponse"];
export type NotificationCreatedData = components["schemas"]["NotificationCreatedData"];
export type ConnectedData = components["schemas"]["ConnectedData"];
export type GapData = components["schemas"]["GapData"];

export type ConversationSummary = components["schemas"]["ConversationSummaryResponse"];
export type DirectMessage = components["schemas"]["DirectMessageResponse"];
export type ConversationListResponse = components["schemas"]["ConversationListResponse"];
export type MessageListResponse = components["schemas"]["MessageListResponse"];
export type SendMessageBody = components["schemas"]["SendMessageBody"];
export type SendMessageResponse = components["schemas"]["SendMessageResponse"];
export type MessageOperationOkResponse = components["schemas"]["MessageOperationOkResponse"];
export type MessageUnreadCountResponse = components["schemas"]["MessageUnreadCountResponse"];

export type FollowResponse = components["schemas"]["FollowResponse"];
export type Presence = components["schemas"]["PresenceResponse"];
export type FeedPage<T> = { items: T[]; nextCursor: string | null };
export type SearchResult = components["schemas"]["SearchResultResponse"];

export type AdminUser = components["schemas"]["AdminUserResponse"];
export type AdminStats = components["schemas"]["AdminStatsResponse"];
export type ReportTarget = components["schemas"]["DiscussionReportTarget"]
  | components["schemas"]["ReplyReportTarget"]
  | components["schemas"]["UserReportTarget"];
export type ReportDTO = components["schemas"]["ReportResponse"];
export type ModerationAction = components["schemas"]["ModerationActionResponse"];
export type DeletedDiscussion = components["schemas"]["DeletedDiscussionResponse"];
export type DeletedReply = components["schemas"]["DeletedReplyResponse"];
export type BoardMember = components["schemas"]["BoardMemberResponse"];

export type FeedbackType = components["schemas"]["FeedbackType"];
export type FeedbackUrgency = components["schemas"]["FeedbackUrgency"];
export type FeedbackStatus = components["schemas"]["FeedbackStatus"];
export type FeedbackItem = components["schemas"]["FeedbackItemResponse"];
export type FeedbackComment = components["schemas"]["CommentResponse"];
export type FeedbackProjectSummary = components["schemas"]["MyProjectResponse"];
export type FeedbackProjectMember = components["schemas"]["MemberResponse"];
export type FeedbackProjectAdmin = components["schemas"]["ProjectAdminResponse"];
export type FeedbackApiKey = components["schemas"]["AgentKeyResponse"];
export type FeedbackBackupInfo = components["schemas"]["BackupFileResponse"];
export type FeedbackBackupSettings = components["schemas"]["BackupSettingsResponse"];

export type TaskPriority = components["schemas"]["TaskPriority"];
export type TaskStatus = components["schemas"]["TaskStatus"];
export type TaskItem = components["schemas"]["TaskItemResponse"];
export type TaskCategoryCount = components["schemas"]["TaskCategoryCount"];
export type TaskComment = components["schemas"]["TaskCommentResponse"];

export type ApiErrorPayload = { code: string; message: string; requestId?: string; details?: unknown };

// ---- 文件服务 / File service ----

// 资料类型：与后端 files_service.KINDS 一一对应。
// Resource kinds, one-to-one with the backend's files_service.KINDS.
export type FileCategory = components["schemas"]["FileCategory"];
export type FileTagCount = components["schemas"]["FileTagCount"];
export type FileResourceSummary = components["schemas"]["FileResourceSummary"];
export type FileResourceDetail = components["schemas"]["FileResourceDetail"];
export type FileResourceList = components["schemas"]["FileResourceList"];
export type FileConfig = components["schemas"]["FileConfig"];
export type FilePresign = components["schemas"]["FilePresign"];
export type FileDownloadTicket = components["schemas"]["FileDownloadTicket"];
export type FileFavoriteState = components["schemas"]["FileFavoriteState"];
export type FileRatingState = components["schemas"]["FileRatingState"];
export type FileKind = FileCategory["kind"];
export type FileVisibility = FileResourceSummary["visibility"];
export type FileSort = FileResourceList["sort"];
export type FileStatus = FileResourceSummary["status"];
// 附件转入命令：与后端 FilePromoteFromAttachmentBody 一一对应（只含前端会填写的字段，
// 并把 visibility 收紧成 FileVisibility 这个既有联合类型）。
// The promote command, one-to-one with the backend's FilePromoteFromAttachmentBody (only the
// fields the client fills in, with visibility narrowed to the existing FileVisibility union).
export type FilePromoteFromAttachmentInput = {
  attachmentId: number;
  categoryId: number;
  title?: string;
  descriptionMarkdown?: string;
  tags?: string[];
  visibility?: FileVisibility;
};

// 上传字节不走 apiFetch：它固定发 JSON，而这里要发原始二进制体。
// Byte upload bypasses apiFetch, which always sends JSON, while this sends raw bytes.
export async function uploadFileBytes(uploadUrl: string, file: File): Promise<void> {
  const res = await fetch(uploadUrl, {
    method: "PUT",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
    credentials: "same-origin",
  });
  if (res.ok) return;
  let payload: ApiErrorPayload = { code: "UPLOAD_FAILED", message: "Upload failed" };
  try {
    const parsed = (await res.json()) as { error?: ApiErrorPayload };
    if (parsed?.error) payload = parsed.error;
  } catch {
    // 非 JSON 响应体（反代错误页等）：保留通用错误信息即可。
    // A non-JSON body (a proxy error page, say): the generic message is enough.
  }
  throw new ApiError(res.status, payload);
}

// 任意 API 返回 401 时广播：AuthProvider 监听后把已登录用户置为登出态。
// Broadcast on any API 401 so AuthProvider can drop logged-in state (login-page
// failures are ignored there because user is already null).
export const SESSION_EXPIRED_EVENT = "samryetha:session-expired";

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

async function apiFetch<T>(path: string, opts: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  const abort = () => controller.abort();
  opts.signal?.addEventListener("abort", abort, { once: true });
  if (opts.signal?.aborted) abort();
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
    if (opts.signal?.aborted) throw error;
    if (controller.signal.aborted) throw new ApiError(0, { code: "TIMEOUT", message: "Request timed out" });
    throw error;
  } finally {
    clearTimeout(timeout);
    opts.signal?.removeEventListener("abort", abort);
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    // 非 JSON 响应：交给错误分支
  }
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    const payload = (data as { error?: ApiErrorPayload })?.error;
    throw new ApiError(res.status, payload ?? { code: "UNKNOWN", message: `Request failed (${res.status})` });
  }
  return data as T;
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
};

export const api = {
  auth: {
    config: () => apiFetch<components["schemas"]["AuthConfigResponse"]>("/api/auth/config"),
    // 嵌入流：不再 302 跳去 Lako，而是把授权参数交回来，由弹层里的 @lako/ui 组件
    // 自己带着凭据跨源去调 Lako 的 POST /api/oauth/authorize。
    oidcStart: (body: { returnTo?: string } = {}) =>
      apiFetch<{ params: Record<string, string> }>("/api/auth/oidc/start", { method: "POST", body }),
    oidcComplete: (body: { code: string; state: string }) =>
      apiFetch<{ status: "ok" } | { status: "claim_required"; ticket: string; claimUrl: string }>(
        "/api/auth/oidc/complete",
        { method: "POST", body },
      ),
    me: () => apiFetch<components["schemas"]["UserEnvelopeResponse"]>("/api/auth/me"),
    login: (body: { username: string; password: string }) =>
      apiFetch<components["schemas"]["AuthSessionResponse"]>("/api/auth/login", { method: "POST", body }),
    logout: () => apiFetch<void>("/api/auth/logout", { method: "POST" }),
    register: (body: { username: string; password: string }) =>
      apiFetch<components["schemas"]["RegisterResponse"]>("/api/auth/register", { method: "POST", body }),
    changePassword: (body: { currentPassword: string; newPassword: string }) =>
      apiFetch<components["schemas"]["AuthOperationOkResponse"]>("/api/auth/change-password", { method: "POST", body }),
    forgotPassword: (body: { username: string; recoveryEmail: string }) =>
      apiFetch<components["schemas"]["PasswordResetRequestResponse"]>("/api/auth/forgot-password", { method: "POST", body }),
    resetPassword: (body: { token: string; newPassword: string }) =>
      apiFetch<components["schemas"]["AuthOperationOkResponse"]>("/api/auth/reset-password", { method: "POST", body }),
    claimInfo: (ticket: string) =>
      apiFetch<components["schemas"]["ClaimInfoResponse"]>(
        `/api/auth/claim?ticket=${encodeURIComponent(ticket)}`,
      ),
    claim: (body: { ticket: string; username: string; password: string }) =>
      apiFetch<components["schemas"]["AuthSessionResponse"]>("/api/auth/claim", { method: "POST", body }),
    claimNew: (body: { ticket: string }) =>
      apiFetch<components["schemas"]["AuthSessionResponse"]>("/api/auth/claim/new", { method: "POST", body }),
    qrStart: () =>
      apiFetch<components["schemas"]["QrStartResponse"]>(
        "/api/auth/qr/start",
        { method: "POST" },
      ),
    qrInfo: (ticketId: string) =>
      apiFetch<components["schemas"]["QrInfoResponse"]>(`/api/auth/qr/info?ticket_id=${encodeURIComponent(ticketId)}`),
    qrRequestCode: (body: { ticket_id: string }) =>
      apiFetch<components["schemas"]["QrConfirmationResponse"]>("/api/auth/qr/confirm/request", {
        method: "POST",
        body,
      }),
    qrApprove: (body: { ticket_id: string; code?: string }) =>
      apiFetch<components["schemas"]["AuthOperationOkResponse"]>("/api/auth/qr/approve", { method: "POST", body }),
    qrDeny: (body: { ticket_id: string }) =>
      apiFetch<components["schemas"]["AuthOperationOkResponse"]>("/api/auth/qr/deny", { method: "POST", body }),
    qrExchange: (body: { ticket_id: string; secret: string }) =>
      apiFetch<components["schemas"]["AuthSessionResponse"]>("/api/auth/qr/exchange", { method: "POST", body }),
  },

  users: {
    get: (username: string) => apiFetch<PublicProfile>(`/api/users/${encodeURIComponent(username)}`),
    posts: (username: string, cursor?: string) =>
      apiFetch<FeedPage<ThreadSummary>>(`/api/users/${encodeURIComponent(username)}/posts${qs({ cursor })}`),
    replies: (username: string, cursor?: string) =>
      apiFetch<FeedPage<ReplyFeedItem>>(`/api/users/${encodeURIComponent(username)}/replies${qs({ cursor })}`),
    saved: (username: string, cursor?: string) =>
      apiFetch<FeedPage<ThreadSummary>>(`/api/users/${encodeURIComponent(username)}/saved${qs({ cursor })}`),
    follow: (username: string) => apiFetch<FollowResponse>(`/api/users/${encodeURIComponent(username)}/follow`, { method: "POST" }),
    unfollow: (username: string) => apiFetch<FollowResponse>(`/api/users/${encodeURIComponent(username)}/follow`, { method: "DELETE" }),
    updateProfile: (patch: { displayName?: string; username?: string; recoveryEmail?: string; bio?: string; settings?: Record<string, boolean | string> }) =>
      apiFetch<{ user: UserDTO }>("/api/me/profile", { method: "PATCH", body: patch }),
  },

  boards: {
    list: () => apiFetch<components["schemas"]["BoardListResponse"]>("/api/boards"),
    get: (slug: string) => apiFetch<BoardSummary>(`/api/boards/${encodeURIComponent(slug)}`),
    create: (body: { name: string; slug: string; description?: string; visibility?: string; postingPolicy?: string }) =>
      apiFetch<BoardSummary>("/api/boards", { method: "POST", body }),
    update: (slug: string, body: { name?: string; description?: string; visibility?: string; postingPolicy?: string }) =>
      apiFetch<BoardSummary>(`/api/boards/${encodeURIComponent(slug)}`, { method: "PATCH", body }),
    del: (slug: string, body: { reason?: string } = {}) =>
      apiFetch<void>(`/api/boards/${encodeURIComponent(slug)}`, { method: "DELETE", body }),
    members: (slug: string) =>
      apiFetch<components["schemas"]["BoardMemberListResponse"]>(`/api/boards/${encodeURIComponent(slug)}/members`),
    updateMemberRole: (slug: string, userId: number, body: { role: "member" | "moderator" }) =>
      apiFetch<void>(`/api/boards/${encodeURIComponent(slug)}/members/${userId}`, { method: "PATCH", body }),
  },

  discussions: {
    vote: (id: number, optionIds: number[]) => apiFetch<PollResponse>(`/api/discussions/${id}/poll/vote`, { method: "PUT", body: { optionIds } }),
    preview: (body: components["schemas"]["PreviewBody"], signal?: AbortSignal) =>
      apiFetch<components["schemas"]["PreviewResponse"]>("/api/discussions/preview", { method: "POST", body, signal }),
    feed: (opts: { feed?: "latest" | "followed"; sort?: MainpageSort; board?: string; cursor?: string; limit?: number }) =>
      apiFetch<components["schemas"]["DiscussionListResponse"]>(`/api/discussions${qs(opts)}`),
    boardFeed: (slug: string, cursor?: string) =>
      apiFetch<FeedPage<ThreadSummary>>(`/api/boards/${encodeURIComponent(slug)}/discussions${qs({ cursor })}`),
    get: (id: number) => apiFetch<DiscussionDetail>(`/api/discussions/${id}`),
    create: (body: components["schemas"]["CreateDiscussionBody"]) =>
      apiFetch<DiscussionDetail>("/api/discussions", { method: "POST", body }),
    update: (id: number, body: components["schemas"]["UpdateDiscussionBody"]) =>
      apiFetch<DiscussionDetail>(`/api/discussions/${id}`, { method: "PATCH", body }),
    del: (id: number) => apiFetch<void>(`/api/discussions/${id}`, { method: "DELETE", body: {} }),
    save: (id: number) => apiFetch<void>(`/api/discussions/${id}/save`, { method: "POST" }),
    unsave: (id: number) => apiFetch<void>(`/api/discussions/${id}/save`, { method: "DELETE" }),
    follow: (id: number) => apiFetch<void>(`/api/discussions/${id}/follow`, { method: "POST" }),
    unfollow: (id: number) => apiFetch<void>(`/api/discussions/${id}/follow`, { method: "DELETE" }),
    pin: (id: number) => apiFetch<void>(`/api/discussions/${id}/pin`, { method: "POST" }),
    lock: (id: number) => apiFetch<void>(`/api/discussions/${id}/lock`, { method: "POST" }),
    replies: (id: number) => apiFetch<components["schemas"]["ReplyListResponse"]>(`/api/discussions/${id}/replies`),
    createReply: (id: number, body: components["schemas"]["CreateReplyBody"]) =>
      apiFetch<ReplyDTO>(`/api/discussions/${id}/replies`, { method: "POST", body }),
    updateReply: (id: number, body: components["schemas"]["UpdateReplyBody"]) => apiFetch<ReplyDTO>(`/api/replies/${id}`, { method: "PATCH", body }),
    delReply: (id: number) => apiFetch<void>(`/api/replies/${id}`, { method: "DELETE", body: {} }),
  },

  drafts: {
    list: (cursor?: string) => apiFetch<components["schemas"]["DraftListResponse"]>(`/api/drafts${qs({ cursor })}`),
    get: (id: number) => apiFetch<DraftDetail>(`/api/drafts/${id}`),
    create: (body: DraftInput) => apiFetch<DraftDetail>("/api/drafts", { method: "POST", body }),
    update: (id: number, body: DraftInput) => apiFetch<DraftDetail>(`/api/drafts/${id}`, { method: "PUT", body }),
    del: (id: number) => apiFetch<components["schemas"]["DraftOperationOkResponse"]>(`/api/drafts/${id}`, { method: "DELETE" }),
  },

  attachments: {
    config: () =>
      apiFetch<{ allowedExtensions: string[]; maxUploadBytes: number }>("/api/attachments/config"),
    presign: (body: { filename: string; mimeType: string; sizeBytes: number }) =>
      apiFetch<{ attachmentId: number; uploadUrl: string; uploadMethod: string; uploadHeaders: Record<string, string> }>(
        "/api/attachments/presign", { method: "POST", body },
      ),
    del: (id: number) => apiFetch<void>(`/api/attachments/${id}`, { method: "DELETE", body: {} }),
  },

  notifications: {
    list: (cursor?: string) =>
      apiFetch<NotificationListResponse>(`/api/notifications${qs({ cursor })}`),
    unreadCount: () => apiFetch<components["schemas"]["UnreadCountResponse"]>("/api/notifications/unread-count"),
    markRead: (id: number) => apiFetch<components["schemas"]["samryetha__notifications__models__OperationOkResponse"]>(`/api/notifications/${id}/read`, { method: "POST" }),
    markAllRead: () => apiFetch<components["schemas"]["samryetha__notifications__models__OperationOkResponse"]>("/api/notifications/read-all", { method: "POST" }),
  },

  messages: {
    conversations: () => apiFetch<ConversationListResponse>("/api/messages/conversations"),
    list: (id: number) => apiFetch<MessageListResponse>(`/api/messages/conversations/${id}`),
    send: (body: SendMessageBody) => apiFetch<SendMessageResponse>("/api/messages", { method: "POST", body }),
    markRead: (id: number) => apiFetch<MessageOperationOkResponse>(`/api/messages/conversations/${id}/read`, { method: "POST" }),
    unreadCount: () => apiFetch<MessageUnreadCountResponse>("/api/messages/unread-count"),
  },

  admin: {
    stats: () => apiFetch<AdminStats>("/api/admin/stats"),
    users: (params: { q?: string; status?: UserStatus; role?: UserRole; excludePending?: boolean; cursor?: number; limit?: number } = {}) =>
      apiFetch<components["schemas"]["AdminUserListResponse"]>(`/api/admin/users${qs(params)}`),
    changeRole: (id: number, body: { role: UserRole; reason?: string }) =>
      apiFetch<AdminUser>(`/api/admin/users/${id}/role`, { method: "PATCH", body }),
    changeStatus: (id: number, body: { status: "active" | "deactivated"; reason?: string }) =>
      apiFetch<AdminUser>(`/api/admin/users/${id}/status`, { method: "PATCH", body }),
    verifyUser: (id: number) => apiFetch<AdminUser>(`/api/admin/users/${id}/verify`, { method: "POST", body: {} }),
    resetPassword: (id: number) => apiFetch<{ temporaryPassword: string }>(`/api/admin/users/${id}/reset-password`, { method: "POST", body: {} }),
    deleteUser: (id: number) => apiFetch<{ ok: boolean }>(`/api/admin/users/${id}`, { method: "DELETE" }),
    deletedContent: (params: { discussionCursor?: number; replyCursor?: number; limit?: number } = {}) =>
      apiFetch<components["schemas"]["DeletedContentResponse"]>(
        `/api/admin/moderation/deleted${qs(params)}`,
      ),
  },

  moderation: {
    createReport: (body: components["schemas"]["CreateReportBody"]) =>
      apiFetch<ReportDTO>("/api/moderation/reports", { method: "POST", body }),
    reviewReport: (id: number, body: components["schemas"]["ReviewReportBody"]) =>
      apiFetch<ReportDTO>(`/api/moderation/reports/${id}/review`, { method: "POST", body }),
    reports: (params: { status?: string; pendingOnly?: boolean; cursor?: number; limit?: number } = {}) =>
      apiFetch<components["schemas"]["ReportListResponse"]>(`/api/moderation/reports${qs(params)}`),
    resolveReport: (id: number, body: { status: string; action?: string; reason?: string }) =>
      apiFetch<ReportDTO>(`/api/moderation/reports/${id}`, { method: "PATCH", body }),
    ban: (body: { username: string; reason?: string; durationHours?: number }) =>
      apiFetch<void>("/api/moderation/bans", { method: "POST", body }),
    unban: (username: string, body: { reason?: string } = {}) =>
      apiFetch<void>(`/api/moderation/bans/${encodeURIComponent(username)}`, { method: "DELETE", body }),
    actions: (params: { cursor?: number; limit?: number } = {}) =>
      apiFetch<components["schemas"]["ModerationActionListResponse"]>(`/api/moderation/actions${qs(params)}`),
    restore: (body: { targetType: "discussion" | "reply"; targetId: number; reason?: string }) =>
      apiFetch<void>("/api/moderation/restore", { method: "POST", body }),
  },

  search: (q: string) => apiFetch<SearchResult>(`/api/search?q=${encodeURIComponent(q)}`),

  presence: {
    heartbeat: () => apiFetch<Presence>("/api/presence/heartbeat", { method: "POST" }),
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

  // 任务（独立站内部页面，仅管理员可见）
  tasks: {
    list: () => apiFetch<{ items: TaskItem[]; categories: TaskCategoryCount[]; canWrite: boolean }>("/api/tasks"),
    create: (body: { category?: string; title: string; notes?: string; priority?: TaskPriority }) =>
      apiFetch<TaskItem>("/api/tasks", { method: "POST", body }),
    update: (id: number, body: { category?: string; title?: string; notes?: string; priority?: TaskPriority }) =>
      apiFetch<TaskItem>(`/api/tasks/${id}`, { method: "PATCH", body }),
    setStatus: (id: number, status: TaskStatus) =>
      apiFetch<TaskItem>(`/api/tasks/${id}/status`, { method: "POST", body: { status } }),
    del: (id: number) => apiFetch<void>(`/api/tasks/${id}`, { method: "DELETE", body: {} }),
    comments: (id: number) => apiFetch<{ items: TaskComment[] }>(`/api/tasks/${id}/comments`),
    createComment: (id: number, body: { body: string; parentCommentId?: number | null }) =>
      apiFetch<TaskComment>(`/api/tasks/${id}/comments`, { method: "POST", body }),
    updateComment: (id: number, body: { body: string }) =>
      apiFetch<TaskComment>(`/api/tasks/comments/${id}`, { method: "PATCH", body }),
    delComment: (id: number) => apiFetch<void>(`/api/tasks/comments/${id}`, { method: "DELETE", body: {} }),
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

  // 文件服务（面向新生的资料库）
  files: {
    config: () => apiFetch<FileConfig>("/api/files/config"),
    list: (params: {
      category?: string;
      kind?: string;
      tag?: string;
      q?: string;
      sort?: FileSort;
      status?: FileStatus;
      featured?: boolean;
      uploaderId?: number;
      page?: number;
      pageSize?: number;
    } = {}) => {
      // 只带上真正有值的查询参数：空串会让后端把它当作一个有效的筛选条件。
      // Only pass parameters that actually carry a value; an empty string would be taken
      // as a real filter by the backend.
      const search = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value === undefined || value === null || value === "" || value === false) continue;
        search.set(key, String(value));
      }
      const query = search.toString();
      return apiFetch<FileResourceList>(`/api/files/resources${query ? `?${query}` : ""}`);
    },
    get: (id: number) => apiFetch<FileResourceDetail>(`/api/files/resources/${id}`),
    download: (id: number) => apiFetch<FileDownloadTicket>(`/api/files/resources/${id}/download`),
    // 预览取地址但不计数：只有真正的下载才应影响下载量这个排序依据。
    // Preview fetches a URL without counting, so only real downloads move the figure the
    // list page sorts by.
    preview: (id: number) => apiFetch<FileDownloadTicket>(`/api/files/resources/${id}/download?preview=true`),
    favorites: () => apiFetch<{ items: FileResourceSummary[]; total: number }>("/api/files/favorites"),
    mine: () => apiFetch<{ items: FileResourceSummary[]; total: number }>("/api/files/mine"),
    setFavorite: (id: number, on: boolean) =>
      apiFetch<FileFavoriteState>(`/api/files/resources/${id}/favorite`, {
        method: on ? "PUT" : "DELETE",
        body: on ? {} : {},
      }),
    setRating: (id: number, score: number) =>
      apiFetch<FileRatingState>(`/api/files/resources/${id}/rating`, { method: "PUT", body: { score } }),
    clearRating: (id: number) =>
      apiFetch<FileRatingState>(`/api/files/resources/${id}/rating`, { method: "DELETE", body: {} }),
    presign: (body: { filename: string; mimeType: string; sizeBytes: number }) =>
      apiFetch<FilePresign>("/api/files/resources/presign", { method: "POST", body }),
    create: (body: {
      objectKey: string;
      expires: string;
      sig: string;
      sizeBytes: number;
      categoryId: number;
      title: string;
      descriptionMarkdown?: string;
      tags?: string[];
      visibility?: FileVisibility;
      originalFilename?: string;
      mimeType?: string;
    }) => apiFetch<FileResourceDetail>("/api/files/resources", { method: "POST", body }),
    // 把论坛附件转入文件服务（管理员专属；非管理员会被后端 403 拒绝）。
    // 标题可缺省：后端会从附件原始文件名推导。
    // Promote a forum attachment into the file service (admins only; the backend rejects
    // non-admins with 403). The title may be omitted: the backend derives it from the
    // attachment's original filename.
    promoteFromAttachment: (body: FilePromoteFromAttachmentInput) =>
      apiFetch<FileResourceDetail>("/api/files/resources/from-attachment", { method: "POST", body }),
    update: (id: number, body: {
      title?: string;
      descriptionMarkdown?: string;
      tags?: string[];
      categoryId?: number;
      visibility?: FileVisibility;
      status?: FileStatus;
    }) => apiFetch<FileResourceDetail>(`/api/files/resources/${id}`, { method: "PATCH", body }),
    del: (id: number) => apiFetch<void>(`/api/files/resources/${id}`, { method: "DELETE", body: {} }),
    createCategory: (body: { slug: string; name: string; description?: string; kind?: FileKind; sortOrder?: number }) =>
      apiFetch<FileCategory>("/api/files/categories", { method: "POST", body }),
    updateCategory: (id: number, body: { name?: string; description?: string; kind?: FileKind; sortOrder?: number }) =>
      apiFetch<FileCategory>(`/api/files/categories/${id}`, { method: "PATCH", body }),
    delCategory: (id: number) => apiFetch<void>(`/api/files/categories/${id}`, { method: "DELETE", body: {} }),
  },
};
