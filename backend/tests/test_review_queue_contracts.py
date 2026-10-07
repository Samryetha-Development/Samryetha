"""OpenAPI contracts for the moderation review queue."""

from samryetha.review_queue.models import ContentType, ModerationDecision, Resolution, ReviewState


def test_review_queue_routes_declare_response_models(api):
    paths = api.app.openapi()["paths"]
    expected = {
        ("/api/admin/moderation/queue", "get"): "QueueListResponse",
        ("/api/admin/moderation/queue/{queue_id}/approve", "post"): "DecisionResponse",
        ("/api/admin/moderation/queue/{queue_id}/reject", "post"): "DecisionResponse",
        ("/api/admin/moderation/finalize", "post"): "FinalizeResponse",
        ("/api/admin/moderation/retained", "get"): "RetainedListResponse",
    }
    for (path, method), schema_name in expected.items():
        response = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response == {"$ref": f"#/components/schemas/{schema_name}"}


def test_review_queue_contract_uses_camel_case_and_stable_values(api):
    schemas = api.app.openapi()["components"]["schemas"]
    item = schemas["QueueItemResponse"]
    assert {"contentType", "contentId", "reviewState", "resolvedByAi", "awaitingHuman"} <= set(item["properties"])
    assert schemas["ContentType"]["enum"] == [member.value for member in ContentType]
    assert schemas["ModerationDecision"]["enum"] == [member.value for member in ModerationDecision]
    assert schemas["ReviewState"]["enum"] == [member.value for member in ReviewState]
    assert schemas["Resolution"]["enum"] == [member.value for member in Resolution]


def test_review_queue_nullability_is_explicit(api):
    schemas = api.app.openapi()["components"]["schemas"]
    for field in ("author", "reviewer", "reviewNote", "reviewedAt", "resolution", "recheck"):
        assert any(branch.get("type") == "null" for branch in schemas["QueueItemResponse"]["properties"][field]["anyOf"])
