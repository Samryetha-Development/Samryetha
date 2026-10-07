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
| `reply.created` | 回复提交成功 | 通知有当前阅读权限的讨论作者、关注者及父回复作者（排除回复者本人）；publish `notification.created` |
| `mention.created` | 讨论或回复提交成功且包含提及 | 仅通知仍有当前阅读权限的被提及用户 |
| `user.followed` | 关注成功 | 通知被关注者；publish `notification.created` |
| `user.banned` | 封禁成功 | 发封禁邮件；publish `user.banned` |
| `discussion.saved` | 收藏成功 | （预留） |
| `discussion.followed` | 关注讨论成功 | （预留） |

### Outbox 行结构

内容提交时在同一写入事务内产生创建及 mention 事件，以现有创建事件作为幂等标记。
`outbox_aggregate_event_idx` 在启动增量补齐时创建，用于定位每个讨论的创建事件。

消费时重新核验父帖/回复删除状态和收件人板块权限，通知标题取当前帖子，不信任事件旧快照。
通知列表、分页和未读数也按当前阅读权限过滤旧 reply/mention 通知，系统通知不受此过滤影响。
不再生成审核暂停状态；旧库升级时将历史 held 内容事件恢复为 pending，保留原事件 ID 及去重关系。

```json
{
  "id": 1,
  "event_type": "reply.created",
  "aggregate_type": "discussion",
  "aggregate_id": "11",
  "payload": "{\"discussionId\":11,\"replyId\":7,\"authorId\":3,\"title\":\"...\"}",
  "status": "done",
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
