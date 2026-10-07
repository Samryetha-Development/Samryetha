"""Contract and validation coverage for the typed notification slice."""

from pydantic import ValidationError
from sqlalchemy import select

from samryetha.notifications.models import NotificationResponse, NotificationType
from samryetha.events.outbox import OutboxWriter
from samryetha.events.outbox_worker import OutboxDispatcher, OutboxDeliveryService, register_outbox_handlers
from samryetha.core.schema import outbox_events


def test_notification_openapi_contract(api):
    schema = api.app.openapi()
    paths = schema["paths"]
    assert paths["/api/notifications"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/NotificationListResponse"
    }
    assert paths["/api/notifications/unread-count"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/UnreadCountResponse"}
    components = schema["components"]["schemas"]
    notification = components["NotificationResponse"]
    assert set(notification["required"]) == {
        "id",
        "type",
        "actor",
        "body",
        "discussionId",
        "replyId",
        "isRead",
        "createdAt",
    }
    assert components["NotificationType"]["enum"] == [member.value for member in NotificationType]
    assert all(member.name[0].isupper() for member in NotificationType)
    assert {"NotificationCreatedData", "ConnectedData", "GapData"} <= set(components)


def test_notification_response_rejects_unknown_type():
    try:
        NotificationResponse.model_validate(
            {
                "id": 1,
                "type": "surprise",
                "actor": None,
                "body": None,
                "discussionId": None,
                "replyId": None,
                "isRead": False,
                "createdAt": 1,
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("unknown notification type must be rejected")


def test_invalid_known_outbox_payload_uses_existing_failure_path(db):
    dispatcher = OutboxDispatcher()
    register_outbox_handlers(dispatcher)
    with db.request_conn() as conn:
        OutboxWriter(conn).emit(
            "reply.created",
            payload={"discussionId": 1, "authorId": 2},
        )
    assert OutboxDeliveryService(db, dispatcher=dispatcher).poll_once(max_attempts=1) == []
    with db.request_conn() as conn:
        row = conn.execute(select(outbox_events.c.status, outbox_events.c.attempts)).one()
    assert row.status == "failed"
    assert row.attempts == 1
