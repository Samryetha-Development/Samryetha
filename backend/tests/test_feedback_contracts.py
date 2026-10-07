"""Feedback OpenAPI and route contract regression tests."""

def test_feedback_routes_declare_response_models(api):
    paths = api.app.openapi()["paths"]
    operations = [
        operation
        for path, methods in paths.items()
        if (path.startswith("/api/feedback") or path.startswith("/api/admin/feedback") or path.startswith("/api/agent/v1"))
        and path != "/api/agent/v1/README"
        for operation in methods.values()
    ]
    assert operations
    assert all(operation["responses"][next(iter(operation["responses"]))]["content"]["application/json"]["schema"] for operation in operations)


def test_feedback_openapi_uses_camel_case_and_enums(api):
    schemas = api.app.openapi()["components"]["schemas"]
    item = schemas["FeedbackItemResponse"]
    assert {"projectId", "closedAt", "editedAt", "createdAt", "updatedAt"} <= set(item["properties"])
    assert schemas["FeedbackType"]["enum"] == ["bug", "suggestion"]
    assert schemas["FeedbackUrgency"]["enum"] == ["urgent", "normal"]
    assert schemas["FeedbackStatus"]["enum"] == ["open", "done", "expired"]
    assert schemas["AgentRole"]["enum"] == ["read", "write"]


def test_feedback_nullable_authors_are_explicit(api):
    schemas = api.app.openapi()["components"]["schemas"]
    for schema_name in ("FeedbackItemResponse", "CommentResponse"):
        author = schemas[schema_name]["properties"]["author"]
        assert any(branch.get("type") == "null" for branch in author["anyOf"])
