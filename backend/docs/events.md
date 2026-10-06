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
| `reply.created` | 回复及父帖均已批准 | 通知有当前阅读权限的讨论作者、关注者及父回复作者（排除回复者本人）；publish `notification.created` |
| `mention.created` | 被提及的讨论/回复及父帖均已批准 | 仅通知仍有当前阅读权限的被提及用户 |
| `user.followed` | 关注成功 | 通知被关注者；publish `notification.created` |
| `user.banned` | 封禁成功 | 发封禁邮件；publish `user.banned` |
| `discussion.saved` | 收藏成功 | （预留） |
| `discussion.followed` | 关注讨论成功 | （预留） |

### Outbox 行结构

待审/被封内容创建时不产生公开通知事件；人工放行或放行编辑时，在写入事务中补发
创建及 mention 事件。父帖放行同时处理已批准的子回复，以现有创建事件作为幂等标记。
`outbox_aggregate_event_idx` 在启动增量补齐时创建，用于定位每个讨论的创建事件。

消费时重新核验父帖/回复审核状态和收件人版块权限，通知标题取当前已批准的帖子，
不信任事件旧快照。已有事件遇到未批准内容转为 `held`，不耗尽失败重试次数；内容
放行后恢复 `pending`，保留原事件 ID，使租约重放仍按 `source_event_id` 去重。
通知列表、分页和未读数也按当前阅读权限过滤旧 reply/mention 通知；审核结果及
系统通知不受此过滤影响。

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
