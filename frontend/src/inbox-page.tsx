import { useEffect, useRef, useState } from "react";
import { AppShell } from "./app-shell";
import { api, ApiError, type AuthorRef, type ConversationSummary, type DirectMessage, type NotificationDTO } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatTime } from "./lib/format";

type InboxTab = "messages" | "notifications";

export function InboxPage() {
  const { user, loading } = useAuth();
  const [tab, setTab] = useState<InboxTab>("messages");

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationsError, setConversationsError] = useState<string | null>(null);
  const [activeConvId, setActiveConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<DirectMessage[]>([]);
  const [otherUser, setOtherUser] = useState<AuthorRef | null>(null);
  const [reply, setReply] = useState("");

  const [notifs, setNotifs] = useState<NotificationDTO[]>([]);
  const [notifsError, setNotifsError] = useState<string | null>(null);
  const [newTo, setNewTo] = useState<string | null>(null);
  const [newBody, setNewBody] = useState("");
  const [messageError, setMessageError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const openRequest = useRef(0);

  const loadConversations = async () => {
    try {
      const d = await api.messages.conversations();
      setConversations(d.items);
      setConversationsError(null);
    } catch {
      setConversationsError("Could not load conversations.");
    }
  };

  const loadNotifications = async () => {
    try {
      const d = await api.notifications.list();
      setNotifs(d.items);
      setNotifsError(null);
    } catch {
      setNotifsError("Could not load notifications.");
    }
  };

  useEffect(() => {
    if (!user) return;
    void loadConversations();
    void loadNotifications();
  }, [user]);

  useEffect(() => {
    // 从 profile 的 Message 按钮跳转而来：读取 ?to=<username> 进入新会话撰写
    // Arrived via the profile Message button: read ?to=<username> to compose a new message
    if (typeof window === "undefined") return;
    const to = new URLSearchParams(window.location.search).get("to");
    if (to) setNewTo(to);
  }, []);

  const openConversation = async (id: number) => {
    const request = ++openRequest.current;
    setActiveConvId(id);
    setMessageError(null);
    try {
      const d = await api.messages.list(id);
      if (request !== openRequest.current) return;
      setMessages(d.items);
      setOtherUser(d.otherUser);
      await api.messages.markRead(id);
      if (request !== openRequest.current) return;
      void loadConversations();
    } catch (err) {
      if (request !== openRequest.current) return;
      setMessageError(err instanceof ApiError ? err.message : "Could not load this conversation.");
    }
  };

  const sendMessage = async () => {
    if (!otherUser || !reply.trim() || !activeConvId || sending) return;
    setSending(true);
    setMessageError(null);
    try {
      await api.messages.send({ username: otherUser.username, body: reply.trim() });
      setReply("");
      await openConversation(activeConvId);
    } catch (err) {
      setMessageError(err instanceof ApiError ? err.message : "Message was not sent. Try again.");
    } finally {
      setSending(false);
    }
  };

  const sendNew = async () => {
    // 新会话：给 ?to 指定的用户发第一条私信，成功后打开该会话
    // New conversation: send the first message to the ?to user, then open it
    if (!newTo || !newBody.trim() || sending) return;
    setSending(true);
    setMessageError(null);
    try {
      const d = await api.messages.send({ username: newTo, body: newBody.trim() });
      setNewBody("");
      setNewTo(null);
      void loadConversations();
      await openConversation(d.conversationId);
    } catch (err) {
      setMessageError(err instanceof ApiError ? err.message : "Message was not sent. Try again.");
    } finally {
      setSending(false);
    }
  };

  const openNotification = (n: NotificationDTO) => {
    void api.notifications.markRead(n.id).then(() => loadNotifications()).catch(() => undefined);
  };

  const markAllRead = async () => {
    await api.notifications.markAllRead();
    loadNotifications();
  };

  if (!loading && !user) {
    return <AppShell><div className="empty-state">Sign in to view your inbox. <a className="sender" href="/login">Sign in</a></div></AppShell>;
  }

  return (
    <AppShell current="inbox">
      <main className="shell inbox-layout">
        <header className="inbox-header">
          <h1 className="feed-title">Inbox</h1>
          <div className="inbox-tabs" role="tablist">
            <button className={`inbox-tab ${tab === "messages" ? "active" : ""}`} role="tab" aria-selected={tab === "messages"} onClick={() => setTab("messages")}>Messages</button>
            <button className={`inbox-tab ${tab === "notifications" ? "active" : ""}`} role="tab" aria-selected={tab === "notifications"} onClick={() => setTab("notifications")}>Notifications</button>
          </div>
        </header>

        {tab === "messages" && (
          <>
            {newTo && (
              <form className="new-message" onSubmit={(e) => { e.preventDefault(); void sendNew(); }}>
                <div className="new-message-head">
                  <strong>New message to @{newTo}</strong>
                  <button type="button" className="action-btn" onClick={() => { setNewTo(null); setNewBody(""); }}>Cancel</button>
                </div>
                <textarea value={newBody} onChange={(e) => setNewBody(e.target.value)} rows={3} maxLength={5000} placeholder="Write your first message…" autoFocus />
                {messageError && <p className="form-error" role="alert">{messageError}</p>}
                <button className="primary-action" type="submit" disabled={sending || !newBody.trim()}>{sending ? "Sending…" : "Send"}</button>
              </form>
            )}
          <div className="inbox-messages">
            <aside className="conversation-list">
              {conversationsError ? (
                <div className="empty-state">{conversationsError} <button type="button" className="action-btn" onClick={() => void loadConversations()}>Try again</button></div>
              ) : conversations.length === 0 ? (
                <div className="empty-state">No messages yet. Visit a profile to start a conversation.</div>
              ) : conversations.map((c) => (
                <button key={c.id} type="button" className={`conversation-item ${activeConvId === c.id ? "active" : ""}`} onClick={() => void openConversation(c.id)}>
                  <span className="conversation-name">{c.otherUser.displayName} <small>@{c.otherUser.handle}</small></span>
                  <span className="conversation-preview">{c.lastMessage ? c.lastMessage.body : "Say hi"}</span>
                  {c.unreadCount > 0 && <span className="badge">{c.unreadCount}</span>}
                </button>
              ))}
            </aside>

            <section className="conversation-thread">
              {activeConvId == null ? (
                <div className="empty-state">Select a conversation.</div>
              ) : (
                <>
                  <div className="conversation-thread-head">{otherUser ? <strong>{otherUser.displayName}</strong> : ""}</div>
                  <div className="message-list">
                    {messages.map((m) => (
                      <div key={m.id} className={`message ${m.senderId === user?.id ? "mine" : "theirs"}`}>
                        <span className="message-body">{m.body}</span>
                        <small className="message-time">{formatTime(m.createdAt)}</small>
                      </div>
                    ))}
                  </div>
                  <form className="message-compose" onSubmit={(e) => { e.preventDefault(); void sendMessage(); }}>
                    {messageError && <p className="form-error" role="alert">{messageError}</p>}
                    <textarea value={reply} onChange={(e) => setReply(e.target.value)} rows={2} maxLength={5000} placeholder="Write a message…" />
                    <button className="primary-action" type="submit" disabled={sending || !reply.trim()}>{sending ? "Sending…" : "Send"}</button>
                  </form>
                </>
              )}
            </section>
          </div>
          </>
        )}

        {tab === "notifications" && (
          <div className="inbox-notifications">
            <div className="inbox-notif-head">
              <button type="button" className="action-btn" onClick={() => void markAllRead()}>Mark all read</button>
            </div>
            <div className="notification-list">
              {notifsError ? (
                <div className="empty-state">{notifsError} <button type="button" className="action-btn" onClick={() => void loadNotifications()}>Try again</button></div>
              ) : notifs.length === 0 ? (
                <div className="empty-state">No notifications.</div>
              ) : notifs.map((n) => (
                n.discussionId ? (
                  <a key={n.id} className={`notification-item ${n.isRead ? "read" : "unread"}`} href={`/d/${n.discussionId}`} onClick={() => openNotification(n)}>
                    <span className="notification-type">{n.type}</span>
                    <span className="notification-body">{n.body ?? (n.actor ? n.actor.displayName : "")}</span>
                    <small>{formatTime(n.createdAt)}</small>
                  </a>
                ) : (
                  <button key={n.id} type="button" className={`notification-item ${n.isRead ? "read" : "unread"}`} onClick={() => openNotification(n)}>
                    <span className="notification-type">{n.type}</span>
                    <span className="notification-body">{n.body ?? (n.actor ? n.actor.displayName : "")}</span>
                    <small>{formatTime(n.createdAt)}</small>
                  </button>
                )
              ))}
            </div>
          </div>
        )}
      </main>
    </AppShell>
  );
}
