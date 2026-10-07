"""OpenAPI and runtime contracts for the typed Draft vertical slice."""

from samryetha.drafts.models import DraftBodyFormat


def test_draft_openapi_contract(api):
    schema = api.app.openapi()
    paths = schema["paths"]
    assert paths["/api/drafts"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DraftListResponse"
    }
    assert paths["/api/drafts"]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DraftDetailResponse"
    }
    assert paths["/api/drafts/{draft_id}"]["delete"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DraftOperationOkResponse"
    }

    components = schema["components"]["schemas"]
    assert components["DraftBodyFormat"]["enum"] == [member.value for member in DraftBodyFormat]
    assert set(components["DraftSummaryResponse"]["required"]) == {
        "id",
        "boardSlug",
        "title",
        "preview",
        "bodyFormat",
        "attachmentCount",
        "createdAt",
        "updatedAt",
    }
    assert set(components["DraftDetailResponse"]["required"]) == {
        "id",
        "boardSlug",
        "title",
        "bodyMarkdown",
        "bodyFormat",
        "attachments",
        "createdAt",
        "updatedAt",
    }


def test_draft_body_format_rejects_unknown_value():
    try:
        DraftBodyFormat("html")
    except ValueError:
        return
    raise AssertionError("unknown draft body formats must be rejected")
