"""Real SQLite transactions: counters must match their authoritative detail rows."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, func, select, update

from samryetha.core.deps import CurrentUser
from samryetha.core.schema import file_downloads, file_favorites, file_ratings, file_resources, users
from samryetha.files import FileService
from test_files import _publish


def _actor(api, username):
    # 角色必须从库里读：这些用例的服务层调用会跑 assert_actor_current，
    # 硬编码一个与库中不一致的角色会被当成"账户角色已变更"而抛冲突。
    # The role has to come from the database: the service calls in these cases run
    # assert_actor_current, and a hard-coded role that disagrees with the stored one is reported as
    # "account role changed" and raises a conflict.
    with api.app.state.db.request_conn() as conn:
        row = conn.execute(select(users.c.id, users.c.role).where(users.c.username == username)).one()
    return CurrentUser(row[0], username, username, f"{username}@example.com", row[1], "active")


def _parallel(api, actor, resource_id, operations):
    barrier = Barrier(len(operations))

    def run(operation):
        barrier.wait(timeout=10)
        with api.app.state.db.request_conn() as conn:
            service = FileService(conn)
            return operation(service, actor, resource_id)

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def _assert_counters(api, resource_id):
    with api.app.state.db.request_conn() as conn:
        row = conn.execute(select(file_resources).where(file_resources.c.id == resource_id)).one()
        count, total = conn.execute(select(func.count(), func.coalesce(func.sum(file_ratings.c.score), 0))
                                   .where(file_ratings.c.resource_id == resource_id)).one()
        favorites = conn.execute(select(func.count()).select_from(file_favorites)
                                 .where(file_favorites.c.resource_id == resource_id)).scalar_one()
    assert (row.rating_count, row.rating_sum, row.favorite_count) == (count, total, favorites)


@pytest.mark.parametrize("clear", [False, True])
def test_concurrent_existing_rating_updates_and_removals(api, clear):
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource_id = _publish(api, "ratings", visibility="public")["id"]
    api.mkuser("bob")
    alice, bob = _actor(api, "alice"), _actor(api, "bob")
    with api.app.state.db.request_conn() as conn:
        FileService(conn).set_rating(alice, resource_id, 1)
        FileService(conn).set_rating(bob, resource_id, 3)
    operation = (lambda s, a, rid: s.clear_rating(a, rid)) if clear else (lambda s, a, rid: s.set_rating(a, rid, 5))
    results = _parallel(api, alice, resource_id, [operation] * 8)
    assert all(result["myRating"] == (None if clear else 5) for result in results)
    _assert_counters(api, resource_id)
    detail = api.c.get(f"/api/files/resources/{resource_id}").json()
    assert detail["ratingCount"] == (1 if clear else 2)
    assert detail["ratingAvg"] == (3 if clear else 4)


def test_concurrent_new_ratings_return_the_written_score(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource_id = _publish(api, "new votes", visibility="public")["id"]
    actor = _actor(api, "alice")
    operations = [(lambda score: lambda s, a, rid: s.set_rating(a, rid, score))(score) for score in [1, 2, 3, 4, 5]]
    results = _parallel(api, actor, resource_id, operations)
    assert [result["myRating"] for result in results] == [1, 2, 3, 4, 5]
    _assert_counters(api, resource_id)


def test_concurrent_favorite_removal_preserves_other_users_favorite(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource_id = _publish(api, "favorites", visibility="public")["id"]
    api.mkuser("bob")
    alice, bob = _actor(api, "alice"), _actor(api, "bob")
    with api.app.state.db.request_conn() as conn:
        FileService(conn).set_favorite(alice, resource_id, True)
        FileService(conn).set_favorite(bob, resource_id, True)
    _parallel(api, alice, resource_id, [lambda s, a, rid: s.set_favorite(a, rid, False)] * 8)
    _assert_counters(api, resource_id)
    assert api.c.get(f"/api/files/resources/{resource_id}").json()["favoriteCount"] == 1


def test_concurrent_download_deduplication_preserves_every_log(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource_id = _publish(api, "downloads", visibility="public")["id"]
    actor = _actor(api, "alice")
    _parallel(api, actor, resource_id, [lambda s, a, rid: s.record_download(a, s.get_resource_row(rid), "127.0.0.1")] * 8)
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(func.count()).select_from(file_downloads)).scalar_one() == 8
        assert conn.execute(select(file_resources.c.download_count).where(file_resources.c.id == resource_id)).scalar_one() == 1


@pytest.mark.parametrize("method", ["set_rating", "clear_rating", "set_favorite", "record_download"])
def test_counter_operations_lock_before_reading(api, method):
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource_id = _publish(api, "lock order", visibility="public")["id"]
    actor = _actor(api, "alice")
    statements = []
    with api.app.state.db.request_conn() as conn:
        service = FileService(conn)
        row = service.get_resource_row(resource_id)
        event.listen(conn, "before_cursor_execute", lambda _c, _cur, sql, _params, _ctx, _many: statements.append(sql))
        if method == "set_rating":
            service.set_rating(actor, resource_id, 5)
        elif method == "clear_rating":
            service.clear_rating(actor, resource_id)
        elif method == "set_favorite":
            service.set_favorite(actor, resource_id, True)
        else:
            service.record_download(actor, row, "127.0.0.1")
    assert statements[0].startswith("UPDATE file_resources SET rating_sum=file_resources.rating_sum")


def test_file_interactions_roll_back_with_the_caller_transaction(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource_id = _publish(api, "rollback", visibility="public")["id"]
    actor = _actor(api, "alice")
    with pytest.raises(RuntimeError, match="rollback"):
        with api.app.state.db.request_conn() as conn:
            service = FileService(conn)
            service.set_rating(actor, resource_id, 5)
            service.set_favorite(actor, resource_id, True)
            raise RuntimeError("rollback")
    _assert_counters(api, resource_id)
    detail = api.c.get(f"/api/files/resources/{resource_id}").json()
    assert detail["ratingCount"] == detail["favoriteCount"] == 0


def test_stale_actor_cannot_write_after_account_changes(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    resource_id = _publish(api, "actor", visibility="public")["id"]
    actor = _actor(api, "alice")
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.id == actor.id).values(status="banned"))
    from samryetha.core.errors import ApiError
    with pytest.raises(ApiError):
        with api.app.state.db.request_conn() as conn:
            FileService(conn).set_rating(actor, resource_id, 5)
    _assert_counters(api, resource_id)
