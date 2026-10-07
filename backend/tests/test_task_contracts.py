"""OpenAPI contracts for the admin task tracker."""

from samryetha.tasks.models import TaskPriority, TaskStatus


def test_task_routes_declare_response_models(api):
    paths = api.app.openapi()["paths"]
    expected = {
        ("/api/tasks", "get"): "TaskListResponse",
        ("/api/tasks", "post"): "TaskItemResponse",
        ("/api/tasks/{id}", "patch"): "TaskItemResponse",
        ("/api/tasks/{id}", "delete"): "TaskOperationOkResponse",
        ("/api/tasks/{id}/status", "post"): "TaskItemResponse",
        ("/api/tasks/{id}/comments", "get"): "TaskCommentListResponse",
        ("/api/tasks/{id}/comments", "post"): "TaskCommentResponse",
        ("/api/tasks/comments/{comment_id}", "patch"): "TaskCommentResponse",
        ("/api/tasks/comments/{comment_id}", "delete"): "TaskOperationOkResponse",
    }
    for (path, method), schema_name in expected.items():
        status = "201" if method == "post" and path in ("/api/tasks", "/api/tasks/{id}/comments") else "200"
        response = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert response == {"$ref": f"#/components/schemas/{schema_name}"}


def test_task_contract_uses_camel_case_and_stable_enum_values(api):
    schemas = api.app.openapi()["components"]["schemas"]
    assert schemas["TaskPriority"]["enum"] == [member.value for member in TaskPriority]
    assert schemas["TaskStatus"]["enum"] == [member.value for member in TaskStatus]
    assert {"doneAt", "createdAt", "updatedAt"} <= set(schemas["TaskItemResponse"]["properties"])
    assert {"taskId", "parentCommentId", "isDeleted"} <= set(schemas["TaskCommentResponse"]["properties"])


def test_task_nullable_fields_are_explicit(api):
    schemas = api.app.openapi()["components"]["schemas"]
    assert any(branch.get("type") == "null" for branch in schemas["TaskItemResponse"]["properties"]["doneAt"]["anyOf"])
    for field in ("parentCommentId", "author"):
        assert any(branch.get("type") == "null" for branch in schemas["TaskCommentResponse"]["properties"][field]["anyOf"])
