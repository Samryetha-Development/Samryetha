"""Search pages and configurable discussion feeds preserve their filters."""

import pytest


def create_post(api, board, title):
    response = api.c.post("/api/discussions", json={"boardSlug": board, "title": title, "bodyMarkdown": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_search_pages_filter_before_count_and_offset(api):
    api.login_dev()
    for slug, visibility in [("paging", "public"), ("other", "public"), ("private", "members")]:
        assert api.c.post("/api/boards", json={"name": slug, "slug": slug, "visibility": visibility}).status_code == 201
    wanted = [create_post(api, "paging", f"needle {i}") for i in range(5)]
    create_post(api, "other", "needle elsewhere")
    create_post(api, "private", "needle private")
    api.mkuser("pagingreader")
    api.login("pagingreader")
    hidden = wanted.pop(2)
    assert api.c.post("/api/moderation/reports", json={"reportableType": "discussion", "reportableId": hidden, "reason": "spam"}).status_code == 201
    pages = []
    for offset in (0, 2, 4):
        response = api.c.get("/api/search", params={"q": "needle", "board": "paging", "limit": 2, "offset": offset})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["total"] == 4
        assert len(result["items"]) == (2 if offset < 4 else 0)
        pages.extend(item["id"] for item in result["items"])
    assert len(pages) == len(set(pages)) == 4
    assert set(pages) == set(wanted)
    global_search = api.c.get("/api/search", params={"q": "needle", "limit": 50}).json()
    assert global_search["total"] == 5  # private and reported content remain invisible
    assert api.c.get("/api/search", params={"q": "needle", "offset": -1}).status_code == 422
    assert api.c.get("/api/search", params={"q": "needle", "offset": 2**64}).status_code == 422
    for limit in (0, 51):
        assert api.c.get("/api/search", params={"q": "needle", "limit": limit}).status_code == 422


def test_followed_search_pages_do_not_fall_back_to_global(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "Paging", "slug": "paging"}).status_code == 201
    first = create_post(api, "paging", "needle first")
    second = create_post(api, "paging", "needle second")
    create_post(api, "paging", "needle unfollowed")
    api.mkuser("pagingreader")
    api.login("pagingreader")
    params = {"q": "needle", "followed": True, "limit": 1}
    assert api.c.get("/api/search", params=params).json() == {"items": [], "total": 0}
    for post_id in (first, second):
        assert api.c.post(f"/api/discussions/{post_id}/follow").status_code == 200
    page1 = api.c.get("/api/search", params=params).json()
    page2 = api.c.get("/api/search", params={**params, "offset": 1}).json()
    assert page1["total"] == page2["total"] == 2
    assert {page1["items"][0]["id"], page2["items"][0]["id"]} == {first, second}
    assert api.c.post("/api/users/dev/follow").status_code == 200
    assert api.c.get("/api/search", params=params).json()["total"] == 3
    api.c.cookies.clear()
    assert api.c.get("/api/search", params=params).json() == {"items": [], "total": 0}


@pytest.mark.parametrize("limit", [1, 2, 50])
def test_feed_and_profile_pages_cover_all_items_at_custom_sizes(api, limit):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "Paging", "slug": "paging"}).status_code == 201
    wanted = [create_post(api, "paging", f"post {i}") for i in range(3)]
    assert api.c.post(f"/api/discussions/{wanted[0]}/pin").status_code == 200
    for post_id in wanted:
        assert api.c.post(f"/api/discussions/{post_id}/save").status_code == 200
    for path in ("/api/discussions", "/api/users/dev/posts", "/api/users/dev/saved", "/api/me/posts"):
        cursor = None
        actual = []
        for _ in range(4):
            params = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            response = api.c.get(path, params=params)
            assert response.status_code == 200, response.text
            page = response.json()
            assert len(page["items"]) <= limit
            actual.extend(item["id"] for item in page["items"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
        assert len(actual) == len(set(actual)) == len(wanted)
        assert set(actual) == set(wanted)
