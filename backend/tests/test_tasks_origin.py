def test_tasks_origin_is_allowed_without_weakening_csrf(client):
    allowed = client.post(
        "/api/tasks",
        headers={"origin": "http://localhost:5300"},
        json={"title": "requires auth"},
    )
    assert allowed.status_code == 401

    rejected = client.post(
        "/api/tasks",
        headers={"origin": "https://evil.example"},
        json={"title": "blocked before auth"},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["message"] == "Cross-origin request rejected"


def test_tasks_origin_cors_preflight(client):
    response = client.options(
        "/api/tasks",
        headers={
            "origin": "http://localhost:5300",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5300"
    assert response.headers["access-control-allow-credentials"] == "true"
