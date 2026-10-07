"""OpenAPI contracts for discussions and replies."""

from samryetha.discussions.models import BodyFormat


def test_discussion_openapi_contracts(api):
    schema = api.app.openapi()
    paths = schema["paths"]
    assert paths["/api/discussions"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DiscussionListResponse"
    }
    assert paths["/api/discussions"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DiscussionDetailResponse"
    }
    assert paths["/api/discussions/{discussion_id}/replies"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReplyResponse"
    }

    components = schema["components"]["schemas"]
    assert components["BodyFormat"]["enum"] == [member.value for member in BodyFormat]
    assert set(components["ThreadSummaryResponse"]["required"]) == {
        "id",
        "title",
        "preview",
        "board",
        "author",
        "replyCount",
        "isPinned",
        "isLocked",
        "createdAt",
        "lastActivityAt",
    }
    assert set(components["ReplyResponse"]["required"]) == {
        "id",
        "discussionId",
        "parentReplyId",
        "author",
        "bodyMarkdown",
        "bodyHtml",
        "bodyFormat",
        "isDeleted",
        "createdAt",
        "updatedAt",
    }
