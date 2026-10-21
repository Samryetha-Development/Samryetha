# Samryetha 领域事件

事件采用两条通道：

1. **outbox（持久可靠）**——业务事务内写 `outbox_events` 行，同事务原子提交。worker 每 500ms 原子 claim 处理，失败指数退避（上限 10 次转 `failed`）。
2. **进程内 EventBus（瞬时）**——outbox 处理完成后 `events.publish()` 唤醒 SSE。SSE 每次都从数据库/在线连接表生成完整状态快照；队列拥塞时合并成一次全量同步，不依赖客户端增量或 HTTP 对账。

命名规范：`subject.verb`（如 `reply.created`）。payload 是 JSON 字符串。

## Outbox 事件

| 事件 | 触发点 | outbox 副作用 |
|------|--------|---------------|
| `user.registered` | 注册成功（pending 用户 + 验证码生成） | 发验证码邮件（console） |
| `password-reset.requested` | 账号与找回邮箱匹配后，与重置令牌在同一事务内写入 | 请求提交后由 worker 发重置邮件；失败按 outbox 策略重试，不回滚令牌 |
| `reply.created` | 回复提交成功 | 通知有当前阅读权限的讨论作者、关注者及父回复作者（排除回复者本人）；publish `notification.created` |
| `mention.created` | 讨论或回复提交成功且包含提及 | 仅通知仍有当前阅读权限的被提及用户 |
| `user.followed` | 关注成功 | 通知被关注者；publish `notification.created` |
| `user.banned` | 封禁成功 | 发封禁邮件；publish `user.banned` |
| `unread.changed` | 标记通知/会话已读成功 | publish `unread.changed`，唤醒该用户的 SSE 未读数快照；与邮件任务独立注册 |
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

定时任务创建、改时和取消不产生公开事件。只有到期并成功发布后，才由普通内容发布流程在同一事务内生成 `discussion.created` 和 mention 事件；失败回滚事件，避免提前通知或重复通知。

| 事件 | 负载 | 订阅者 |
|------|------|--------|
| `notification.created` | `{ userId }` | 唤醒该用户的 SSE `unread-count` 快照 |
| `unread.changed` | `{ userId }` | 标记通知/会话已读后刷新该用户的 SSE `unread-count` 快照 |
| `presence.changed` | `{ onlineCount }` | 首个 SSE 连接上线或最后一个连接断开时刷新所有连接的 `presence` 快照 |
| `user.banned` | `{ userId }` | SSE（预留） |

同一用户的多个标签页按用户去重，不会重复增加在线人数。多实例部署时，需把 EventBus 与 presence 租约换成 Redis 等共享实现；当前单实例直接使用内存状态。
