"""OpenAPI coverage for typed system and search endpoints."""


def test_system_and_search_routes_declare_response_models(api):
    paths = api.app.openapi()["paths"]
    expected = {
        ("/api/health", "get"): "HealthResponse",
        ("/api/users/{username}/follow", "post"): "FollowResponse",
        ("/api/users/{username}/follow", "delete"): "FollowResponse",
        ("/api/presence/heartbeat", "post"): "PresenceResponse",
        ("/api/presence", "get"): "PresenceResponse",
        ("/api/search", "get"): "SearchResultResponse",
    }
    for (path, method), schema_name in expected.items():
        response = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response == {"$ref": f"#/components/schemas/{schema_name}"}


def test_search_result_has_complete_thread_rendering_contract(api):
    schemas = api.app.openapi()["components"]["schemas"]
    item = schemas["SearchItemResponse"]
    assert {
        "id",
        "title",
        "preview",
        "board",
        "author",
        "replyCount",
        "isPinned",
        "isLocked",
        "moderationStatus",
        "createdAt",
        "lastActivityAt",
    } == set(item["required"])
    assert item["properties"]["moderationStatus"] == {
        "$ref": "#/components/schemas/ModerationStatus"
    }
