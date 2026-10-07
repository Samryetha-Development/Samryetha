"""OpenAPI contracts for the typed board, user, moderation, and admin slices."""

from samryetha.admin.models import AdminAssignableRole, AdminMutableStatus
from samryetha.boards.models import BoardMemberRole, BoardVisibility, PostingPolicy
from samryetha.moderation.models import ReportableType, ReportStatus, RestoreTargetType
from samryetha.users.models import AccountRole, AccountStatus


def _response_ref(paths, path: str, method: str, name: str) -> None:
    assert paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{name}"
    }


def test_typed_route_response_models(api):
    paths = api.app.openapi()["paths"]
    expected = {
        ("/api/boards", "get"): "BoardListResponse",
        ("/api/boards/{slug}", "get"): "BoardSummaryResponse",
        ("/api/users/{username}", "get"): "PublicProfileResponse",
        ("/api/me/profile", "patch"): "UserEnvelopeResponse",
        ("/api/moderation/reports", "get"): "ReportListResponse",
        ("/api/moderation/reports/{id}", "patch"): "ReportResponse",
        ("/api/moderation/actions", "get"): "ModerationActionListResponse",
        ("/api/admin/stats", "get"): "AdminStatsResponse",
        ("/api/admin/users", "get"): "AdminUserListResponse",
        ("/api/admin/moderation/deleted", "get"): "DeletedContentResponse",
    }
    for (path, method), model in expected.items():
        _response_ref(paths, path, method, model)


def test_typed_domain_enums_keep_wire_values_and_pascal_names(api):
    schemas = api.app.openapi()["components"]["schemas"]
    enums = (
        BoardVisibility, PostingPolicy, BoardMemberRole, AccountRole, AccountStatus,
        ReportableType, ReportStatus, RestoreTargetType, AdminAssignableRole, AdminMutableStatus,
    )
    for enum in enums:
        assert schemas[enum.__name__]["enum"] == [member.value for member in enum]
        assert all(member.name[0].isupper() for member in enum)


def test_typed_contracts_use_camel_case(api):
    schemas = api.app.openapi()["components"]["schemas"]
    assert {"displayName", "emailVerified", "lastSeenAt", "banActive", "reportCount"} <= set(
        schemas["AdminUserResponse"]["properties"]
    )
    assert {"reportableType", "reportableId", "createdAt"} <= set(schemas["ReportResponse"]["properties"])
    assert {"nextDiscussionCursor", "nextReplyCursor"} <= set(schemas["DeletedContentResponse"]["properties"])
    assert {"postingPolicy", "memberCount", "currentUserRole"} <= set(schemas["BoardSummaryResponse"]["properties"])
