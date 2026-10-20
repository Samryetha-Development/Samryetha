"""2026-10-18 安全审计 F01-F08 回归测试（A 批：F01/F02/F03/F04/F07/F08）。

断言一律是"攻击被拒绝"的正向守卫，而不是"能复现漏洞"的现象断言：把对应修复回退后，
这些用例必须失败（变红）。每条都对应报告里的一个 finding。

Tests here assert the fixed behaviour ("the attack is refused") rather than the vulnerable
behaviour; reverting any fix must turn the matching test red.
"""

from __future__ import annotations

from sqlalchemy import insert, select

from samryetha.auth import AuthService
from samryetha.core.config import Settings
from samryetha.core.db import now_ms
from samryetha.core.schema import board_members, boards, users


# ---------------------------------------------------------------- F01 私有板块受控入会


def test_f01_private_board_self_join_rejected(api):
    api.login_dev()
    assert (
        api.c.post("/api/boards", json={"name": "P", "slug": "audit-private", "visibility": "private"}).status_code
        == 201
    )
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "audit-private", "title": "secret", "bodyMarkdown": "private-secret"}
    ).json()["id"]
    api.mkuser("intruder")
    api.login("intruder")
    # 非成员：板块 404、读帖 403
    assert api.c.get("/api/boards/audit-private").status_code == 404
    assert api.c.get(f"/api/discussions/{did}").status_code == 403
    # 攻击：普通账号自行加入 private 板块 → 必须被拒
    r = api.c.post("/api/boards/audit-private/join")
    assert r.status_code == 403, r.text
    # 加入失败后仍读不到私密正文
    assert api.c.get(f"/api/discussions/{did}").status_code == 403


def test_f01_authorized_member_can_read_private(api):
    api.login_dev()
    assert (
        api.c.post("/api/boards", json={"name": "P2", "slug": "audit-private2", "visibility": "private"}).status_code
        == 201
    )
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "audit-private2", "title": "s", "bodyMarkdown": "member-secret"}
    ).json()["id"]
    api.mkuser("granted")
    # 管理员经受控路径授予成员身份（直接落库模拟邀请）
    with api.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "granted")).scalar_one()
        bid = conn.execute(select(boards.c.id).where(boards.c.slug == "audit-private2")).scalar_one()
        conn.execute(board_members.insert().values(board_id=bid, user_id=uid, role="member", joined_at=now_ms()))
    api.login("granted")
    # 已授权成员可读
    assert api.c.get(f"/api/discussions/{did}").status_code == 200


def test_f01_members_board_still_self_joinable(api):
    api.login_dev()
    assert (
        api.c.post("/api/boards", json={"name": "M", "slug": "audit-members", "visibility": "members"}).status_code
        == 201
    )
    api.mkuser("joiner")
    api.login("joiner")
    # members 板块保持自行加入策略不变
    assert api.c.post("/api/boards/audit-members/join").status_code == 200


# ---------------------------------------------------------------- F02 反馈作者撤权后旁路


def test_f02_removed_author_cannot_mutate_or_read_new_detail(api):
    api.login_dev()
    pid = api.c.post("/api/feedback/projects", json={"name": "P", "description": ""}).json()["id"]
    api.mkuser("author")
    with api.app.state.db.request_conn() as conn:
        author_id = conn.execute(select(users.c.id).where(users.c.username == "author")).scalar_one()
    assert (
        api.c.put(
            f"/api/feedback/projects/{pid}/members",
            json={"members": [{"userId": author_id, "isProgrammer": False}]},
        ).status_code
        == 200
    )
    api.login("author")
    iid = api.c.post(
        "/api/feedback", json={"projectId": pid, "title": "bug", "detail": "old", "type": "bug"}
    ).json()["id"]
    cid = api.c.post(f"/api/feedback/{iid}/comments", json={"body": "c"}).json()["id"]
    # 管理员把 author 移出项目，并改写详情
    api.login_dev()
    assert api.c.put(f"/api/feedback/projects/{pid}/members", json={"members": []}).status_code == 200
    assert api.c.patch(f"/api/feedback/{iid}", json={"detail": "new-secret-after-removal"}).status_code == 200
    # 被移出的 author：一切写/读旁路都被拒
    api.login("author")
    assert api.c.patch(f"/api/feedback/{iid}", json={}).status_code == 403
    assert api.c.patch(f"/api/feedback/{iid}", json={"detail": "x"}).status_code == 403
    assert api.c.delete(f"/api/feedback/{iid}").status_code == 403
    assert api.c.patch(f"/api/feedback/comments/{cid}", json={"body": "y"}).status_code == 403
    assert api.c.delete(f"/api/feedback/comments/{cid}").status_code == 403


# ---------------------------------------------------------------- F03 Agent 非法项目范围


def test_f03_invalid_agent_scope_rejected(api):
    api.login_dev()
    p1 = api.c.post("/api/feedback/projects", json={"name": "A", "description": ""}).json()["id"]
    # 非法/已删项目 ID → 整体拒绝（不得静默过滤成"全项目"）
    assert (
        api.c.post("/api/admin/feedback/keys", json={"name": "k", "role": "read", "projectIds": [999999]}).status_code
        == 400
    )
    # 部分合法部分非法 → 同样整体拒绝
    assert (
        api.c.post(
            "/api/admin/feedback/keys", json={"name": "k2", "role": "read", "projectIds": [p1, 999999]}
        ).status_code
        == 400
    )
    # 空列表 = 显式全项目（唯一允许的全局授权方式）
    assert api.c.post("/api/admin/feedback/keys", json={"name": "k3", "role": "read", "projectIds": []}).status_code == 201


# ---------------------------------------------------------------- F04 未发布附件下载


def test_f04_unpublished_attachment_requires_active_uploader(api):
    api.mkuser("uploader")
    api.login("uploader")
    pres = api.c.post(
        "/api/attachments/presign", json={"filename": "s.txt", "mimeType": "text/plain", "sizeBytes": 4}
    ).json()
    assert api.c.put(pres["uploadUrl"], content=b"test").status_code == 204
    url = api.c.get(f"/api/attachments/{pres['attachmentId']}").json()["downloadUrl"]
    # 上传者本人（active）可下载
    assert api.c.get(url).status_code == 200
    # 访客（无会话）下载未发布附件 → 拒绝
    api.c.post("/api/auth/logout")
    assert api.c.get(url).status_code == 404
    # 封禁上传者后，旧 URL 失效
    api.login_dev()
    assert api.c.post("/api/moderation/bans", json={"username": "uploader", "reason": "spam"}).status_code == 200
    api.c.post("/api/auth/logout")
    assert api.c.get(url).status_code == 404


# ---------------------------------------------------------------- F07 内建身份不可变标记


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_f07_builtin_activation_preserves_password(db):
    settings = _settings()
    with db.request_conn() as conn:
        AuthService(conn, settings=settings).ensure_builtin_accounts()
        dev_hash = conn.execute(select(users.c.password_hash).where(users.c.username == "dev")).scalar_one()
        assert conn.execute(select(users.c.is_builtin).where(users.c.username == "dev")).scalar_one() == 1
    # 再次启动（模拟重启）：真内建激活、密码不被重置、角色保持 admin
    with db.request_conn() as conn:
        AuthService(conn, settings=settings).ensure_builtin_accounts()
        assert conn.execute(select(users.c.password_hash).where(users.c.username == "dev")).scalar_one() == dev_hash
        assert conn.execute(select(users.c.role).where(users.c.username == "dev")).scalar_one() == "admin"


def test_f07_impostor_dev_not_promoted(db, caplog):
    settings = _settings()
    # 模拟攻击：真 dev 被删后，冒名者以 role=student、未验证、is_builtin=0 占用了 dev
    with db.request_conn() as conn:
        conn.execute(
            insert(users).values(
                username="dev",
                display_name="dev",
                email="dev@samryetha.local",
                password_hash="x",
                role="student",
                status="active",
                discriminator=4242,
                settings="{}",
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )
    # 新逻辑：拒绝提升 + 显著告警 + 不给管理员权限
    with db.request_conn() as conn:
        AuthService(conn, settings=settings).ensure_builtin_accounts()
        role = conn.execute(select(users.c.role).where(users.c.username == "dev")).scalar_one()
        assert role == "student"
    assert any("[SECURITY][F07]" in rec.message for rec in caplog.records)


def test_f07_backfill_marks_only_genuine_builtin(db):
    # 真 admin（role=admin + 已验证）+ 冒名 dev（role=student + 未验证）
    with db.request_conn() as conn:
        conn.execute(
            insert(users).values(
                username="admin",
                display_name="admin",
                email="admin@samryetha.local",
                password_hash="x",
                role="admin",
                status="active",
                email_verified_at=now_ms(),
                discriminator=1001,
                settings="{}",
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )
        conn.execute(
            insert(users).values(
                username="dev",
                display_name="dev",
                email="dev@samryetha.local",
                password_hash="x",
                role="student",
                status="pending",
                discriminator=1002,
                settings="{}",
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )
    db.mark_builtin_identity()
    with db.request_conn() as conn:
        admin_flag = conn.execute(select(users.c.is_builtin).where(users.c.username == "admin")).scalar_one()
        dev_flag = conn.execute(select(users.c.is_builtin).where(users.c.username == "dev")).scalar_one()
    assert admin_flag == 1  # 真内建被标记
    assert dev_flag == 0  # 冒名者未被标记


def test_f07_reserved_name_register_rejected(api):
    # 保留名 admin/dev 不得注册
    assert api.c.post("/api/auth/register", json={"username": "dev", "password": "password123"}).status_code == 409
    assert api.c.post("/api/auth/register", json={"username": "admin", "password": "password123"}).status_code == 409


def test_f07_reserved_name_rename_rejected(api):
    api.mkuser("victim")
    api.login("victim")
    assert api.c.patch("/api/me/profile", json={"username": "dev"}).status_code == 409


# ---------------------------------------------------------------- F08 删除板块后内容入口


def test_f08_deleted_board_content_inaccessible(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "D", "slug": "audit-del", "visibility": "public"}).status_code == 201
    did = api.c.post("/api/discussions", json={"boardSlug": "audit-del", "title": "t", "bodyMarkdown": "body"}).json()[
        "id"
    ]
    api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "reply"})
    # 管理员删除板块
    assert api.c.request("DELETE", "/api/boards/audit-del", json={"reason": "cleanup"}).status_code == 200
    # 访客读详情/回复全部被拒
    api.c.post("/api/auth/logout")
    assert api.c.get(f"/api/discussions/{did}").status_code == 404
    assert api.c.get(f"/api/discussions/{did}/replies").status_code == 404
    # active 账号也不能再回帖
    api.mkuser("poster")
    api.login("poster")
    assert api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "new"}).status_code == 404
