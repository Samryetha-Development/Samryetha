# Samryetha 领域事件

事件采用两条通道：

1. **outbox（持久可靠）**——业务事务内写 `outbox_events` 行，同事务原子提交。worker 每 500ms 原子 claim 处理，失败指数退避（上限 10 次转 `failed`）。
2. **进程内 EventBus（瞬时）**——outbox 处理完成后 `events.publish()`，SSE hub 订阅推送。队列溢出时发送 `gap`，客户端收到后重拉未读数。

命名规范：`subject.verb`（如 `reply.created`）。payload 是 JSON 字符串。

## Outbox 事件

| 事件 | 触发点 | outbox 副作用 |
|------|--------|---------------|
| `user.registered` | 注册成功（pending 用户 + 验证码生成） | 发验证码邮件（console） |
| `user.password_reset_requested` | 忘记密码提交 | 发重置邮件（console） |
| `reply.created` | 回复成功 | 通知讨论作者 + 讨论关注者（排除回复者本人）；publish `notification.created` |
| `user.followed` | 关注成功 | 通知被关注者；publish `notification.created` |
| `user.banned` | 封禁成功 | 发封禁邮件；publish `user.banned` |
| `discussion.saved` | 收藏成功 | （预留） |
| `discussion.followed` | 关注讨论成功 | （预留） |

### Outbox 行结构

```json
{
  "id": 1,
  "event_type": "reply.created",
  "aggregate_type": "discussion",
  "aggregate_id": "11",
  "payload": "{\"discussionId\":11,\"replyId\":7,\"authorId\":3,\"title\":\"...\"}",
  "status": "processed",
  "attempts": 0,
  "available_at": 1788022289371,
  "created_at": 1788022289371,
  "processed_at": 1788022289871
}
```

## EventBus（瞬时通道）

| 事件 | 负载 | 订阅者 |
|------|------|--------|
| `notification.created` | `{ userId }` | SSE `/api/events`（按 userId 过滤推送） |
| `user.banned` | `{ userId }` | SSE（预留） |

多实例部署时，把 `EventBus` 实现换成 Redis pub/sub（消息结构不变），SSE 各实例转发自身连接的用户。当前单实例直接内存广播。
