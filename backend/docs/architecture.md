# Samryetha 后端架构

## 1. 运行与边界

Samryetha 是模块化单体：FastAPI、Pydantic v2、SQLAlchemy 2.0 Core 与 SQLite。
Python 要求 >=3.12，依赖由 uv 管理；后端直接运行源码，没有 TypeScript 后端或后端构建步骤。
论坛前端是独立的 React/Vite SSR 客户端；Lako 身份服务与 `@lako/ui` 独立版本管理。

`core/schema.py` 是数据库表的唯一真源，`core/db.py` 管理连接、请求事务、
SQLite WAL/foreign_keys/busy_timeout 和存量库的 schema drift。
`core/app.py` 装配 FastAPI、适配器和路由；`main.py` 启动后台 worker。

## 2. 职责与依赖方向

```text
Router / Worker / 跨域调用方
             ↓
Application Service 实例（真实用例实现）
             ↓
具体 Repository 实例（SQL、查询条件、数据库 record）
             ↓
SQLAlchemy Core / SQLite
```

Application Service 必须包含用例的实际实现，不能只是包装旧的过程式用例函数。
任何涉及持久化的业务验证、授权协调、多个 repository 操作、事件/通知编排或结果组装，
都应在领域服务的方法中找到。实例持有当前请求的 Connection；需要配置、Storage 或 Mailer
时同时持有这些依赖，方法不重复接收 Connection。

Repository 的实际持久化实现属于具体类，由实例持有当前 Connection；方法不再重复传 conn。
它拥有 SQL、查询组合以及 RowMapping 到 typed record 的转换。
转换统一使用 `core.records` 的标量解析器，保留 typed ID。
服务在构造时用同一个连接创建所需 Repository 实例，但不能自己执行 SQL，也不能要求 repository
隐藏提交或回滚。没有 BaseService、泛型 Repository、Factory、UnitOfWork 或 DI 容器。

Router 负责 HTTP 参数、依赖获取、cookie、状态码、响应模型和流式传输协议，调用服务实例。
鉴权依赖适合放在 HTTP 边界；业务内的资源权限核验由服务通过 AuthorizationService 协调。
跨域调用走公开服务/模型，不直接导入另一领域的私有表或 repository。
依赖仅用于注解时使用 TYPE_CHECKING；确实存在相互编排时保留必要的局部导入。

普通函数适合纯规则、计算、谓词和转换，例如密码/令牌哈希、用户名规范化、DTO 转换、
游标解析和 Markdown 渲染。不要为这些函数创建只有一个纯方法的类。

### 正确与错误示例

正确：实现属于类，连接属于实例，SQL 属于 repository。

```python
class TaskService:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = TaskRepository(conn)

    def update_task(self, task_id: TaskID, command: TaskPatch) -> TaskItemResponse:
        values = {"updated_at": now_ms()}
        # 实际字段规则在这里；repository 执行更新和读取。
        if "title" in command.model_fields_set and command.title is not None:
            values["title"] = command.title
        if not self._repository.update_task(task_id, values):
            raise not_found("Task not found")
        ...
```

错误：下面只是 façade，旧函数仍拥有实现，不算迁移。

```python
def update_task(conn, task_id, command):
    ...  # 真实用例仍在这里

class TaskService:
    def update_task(self, task_id, command):
        return update_task(self._conn, task_id, command)
```

兼容接口只有存在真实、尚不能迁移的调用方时才保留，必须注明原因与弃用方向，
且只能 `旧函数 → 服务实例方法`，不能反向委托。
当前仓库的调用方与测试已迁移，没有保留顶层 legacy 用例兼容适配器。
NotificationService 的参数便利方法在类内转换为 typed command/page，既不持有独立旧实现，
也不构成顶层兼容入口。

## 3. 服务导航

持久化层共 17 个领域 Repository：AdminRepository、AttachmentRepository、AuthRepository、
OidcRepository、QrAuthRepository、AuthorizationRepository、BoardRepository、
DiscussionRepository、DraftRepository、EventRepository、FeedbackRepository、MessageRepository、
ModerationRepository、NotificationRepository、SearchRepository、TaskRepository、
UserRepository。另有 DatabaseSnapshotRepository（持有 Database，使用专用 AUTOCOMMIT 连接）
和 OutboxWriter（持有请求 Connection，同事务写事件）。没有保留旧的顶层持久化函数适配器。

```python
class OidcRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def username_exists(self, username: str) -> bool:
        return self._conn.execute(select(users.c.id).where(users.c.username == username)).first() is not None
```

Repository 不创建请求事务、不 commit/rollback/close 调用方连接，也不包含权限、通知或
应用用例决策。纯标量/record 转换仍为函数，不创建通用 CRUD、Repository 基类或工厂。

| 领域 | 实际应用入口 |
| --- | --- |
| Tasks | TaskService：任务、状态与嵌套评论 |
| Discussions | DiscussionService：讨论/回复、feed、收藏/关注、附件/草稿消费 |
| Auth | AuthService：本地注册/密码登录、密码恢复、内置账号、扫码会话兑换、紧急登录 |
| Sessions | auth/sessions.py 的 SessionService：会话创建、读取和撤销；security.py 只保留哈希等工具 |
| OIDC | auth/oidc.py 的 OidcService：事务、身份映射、认领与账户；OidcLoginService：跨事务回调流程 |
| QR auth | auth/qr_login.py 的 QrAuthService：票据、确认码、邮件交付及批准 |
| Users / Follows | UserService / FollowService：资料、用户查找与创建、关注 |
| Messages | MessageService：发送、会话列表、读取和未读数 |
| Boards / Admin | BoardService / AdminService：分区成员管理、管理员操作与审计 |
| Moderation | ModerationService：举报、人工封禁、软删除恢复与治理审计 |
| Feedback | FeedbackService：项目/成员、条目/评论与 Agent Key |
| Backup | feedback/backup.py 的 BackupSettingsService / BackupService：事务设置与数据库快照/恢复申请 |
| Drafts / Attachments | DraftService / AttachmentService：私人草稿与附件生命周期、权限、清理 |
| Streaming upload | AttachmentUploadService：私有临时文件写入与短事务原子发布 |
| Notifications / Search | NotificationService / SearchService：通知读写与受可见性约束的搜索 |
| Events | ContentEventService：内容发布幂等协调；OutboxEventService：通知/邮件处理；OutboxDeliveryService：claim、重试与广播 |

这些服务没有继承层次。部分查询方法很短，但它们属于具有持久化/权限上下文的应用接口，
不是把纯函数包成类。领域不必机械具有相同文件布局：OIDC、QR、备份和事件按实际职责分文件。

## 4. 事务与可观察行为

请求依赖在 `Database.request_conn()` 中取得连接。正常返回提交，异常回滚；
一般请求服务不自行提交、关闭连接或重新开事务。业务行与 outbox 行使用同一连接，
同事务原子提交。跨域服务实例也共享此连接，不通过新的连接绕过请求事务。

特殊生命周期必须保持明确：

- OidcLoginService 先独立消费并提交一次性 state，再向 IdP 换 token，最后在新事务认身份。
  避免网络等待持有写锁，也防止重放。Cookie/state 的 HTTP 校验仍在 Router。
  OidcService 的认领尝试编排在密码错误时单独提交失败次数，原认领事务仍由请求异常回滚。
- AttachmentUploadService 先只读检查上传票据，再流式写私有临时文件；完成后使用短事务
  原子竞争上传状态，只有获胜请求发布文件。不得在接收请求流期间持有 SQLite 写锁。
- BackupService 通过 DatabaseSnapshotRepository 的专用 AUTOCOMMIT 连接执行 VACUUM INTO，
  不使用请求事务；恢复只写待恢复标记，启动时打开数据库前替换文件。
- OutboxDeliveryService 的 claim、逐事件处理和失败记录分别使用已有的短事务边界。
- 编辑核验内容版本，发生变化返回 CONFLICT；取得写锁后再次核验作者状态、角色和权限。

Feedback 条目/评论、Boards 资源权限核验在领域服务中完成；Agent Key 的项目过滤也在
FeedbackService 内。用户 feed 的账号解析由 UserService 编排，讨论详情的附件查询与结果
组装由持有 Storage 的 DiscussionService 完成。Router 不再拼接这些持久化步骤。

本次移除内容审核，相关 HTTP 路由与状态字段不再出现在 OpenAPI；生成的客户端类型同步更新。

## 5. 保留为函数的规则与基础设施

- auth/security.py：Argon2id、dummy 校验与 SHA-256 令牌哈希，不访问数据库。
- 用户名/handle、preview、DTO/JSON 转换：无应用状态的纯函数。
- adapters/markdown.py 与 markdown_math.py：渲染 Markdown/TeX，纯处理不触发发布事件。
  服务端切结构/净化，客户端执行 KaTeX/Mermaid 排版。
- events/outbox.py 的 OutboxWriter：明确的事务内持久化基础设施，不是业务用例；
  实例持有调用方 Connection，emit 方法保证事件与调用方事务原子写入。
  未使用的 core/db.py 顶层 run_scalar 已移除，不添加无调用方的替代包装。
- events/outbox_worker.py 的 register_outbox_handlers：应用装配，将 typed handler 绑定到
  服务方法；payload 解析与实时事件构造也是无状态函数。
- feedback/backup.py 的 apply_pending_restore：引擎创建前的文件恢复基础设施，
  此时没有请求连接；时间戳、cron 解析和备份文件清理是局部基础设施工具。
- core/db.py 的 schema/PRAGMA/事务维护，以及 system/health_router.py 的 SELECT 1：
  明确的数据库基础设施和健康探针，允许直接 SQL。
- OutboxWorker、BackupScheduler：启动、停止和调度驱动器。
- MemoryPresenceStore、EventBus、OidcClient、Mailer、Storage：已有基础设施抽象。

## 6. 保持的安全与功能边界

OIDC 只证明身份；论坛按 (issuer, subject) 绑定本地资料并发自己的 session。
state 服务端只存哈希，token 不写浏览器存储；会话 token 为 32 随机字节 base64url，
数据库只保存 SHA-256，cookie 使用 HttpOnly/SameSite 与现有 secure/domain 配置。

内容直接发布，不再调用审核规则或外部模型。讨论、回复、搜索、收藏、通知与附件仍遵守板块权限和删除状态。举报、人工封禁及身份验证保持不变。
私人草稿仅作者可访问，发布时校验所有权/附件并与草稿消费一起原子提交。

## 7. 运行与验证

```powershell
# 仓库根：安装、迁移、开发数据与双服务
python bootstrap.py --dev

# backend/
uv run python -m samryetha.main
uv run pytest
uv run ruff check .
uv run basedpyright
uv run python scripts/audit_architecture.py

# frontend/
pnpm typecheck
pnpm build

# 仓库根
git diff --check
```

后端默认 3001；前端 SSR 开发/生产入口独立，配置见各自环境文件与现有部署脚本。
不要把生成产物、数据库、上传文件或 secrets 加入版本控制。

架构检查覆盖：模块级持久化函数/用例、类回调旧用例、服务内 SQL/查询组合、Router 绕过服务、
实例方法重复接受/传递连接、公共导出一致性以及独立进程的模块导入循环。
测试覆盖 API、OpenAPI contracts、权限/可见性、会话、旧库内容恢复、附件上传竞争与 outbox
延期/重试；结构检查不能代替行为测试。
