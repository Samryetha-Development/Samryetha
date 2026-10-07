"""OpenAPI contracts for password, OIDC migration, and QR authentication."""


def _response_ref(paths, path: str, method: str, name: str, status: str = "200") -> None:
    assert paths[path][method]["responses"][status]["content"]["application/json"]["schema"] == {
        "$ref": f"#/components/schemas/{name}"
    }


def test_auth_routes_declare_response_models(api):
    paths = api.app.openapi()["paths"]
    expected = {
        ("/api/auth/config", "get"): "AuthConfigResponse",
        ("/api/auth/login", "post"): "AuthSessionResponse",
        ("/api/auth/me", "get"): "UserEnvelopeResponse",
        ("/api/auth/claim", "get"): "ClaimInfoResponse",
        ("/api/auth/claim", "post"): "AuthSessionResponse",
        ("/api/auth/claim/new", "post"): "AuthSessionResponse",
        ("/api/auth/qr/start", "post"): "QrStartResponse",
        ("/api/auth/qr/info", "get"): "QrInfoResponse",
        ("/api/auth/qr/exchange", "post"): "AuthSessionResponse",
        ("/api/auth/change-password", "post"): "AuthOperationOkResponse",
        ("/api/auth/reset-password", "post"): "AuthOperationOkResponse",
    }
    for (path, method), model in expected.items():
        _response_ref(paths, path, method, model)
    _response_ref(paths, "/api/auth/register", "post", "RegisterResponse", "201")


def test_auth_contracts_preserve_wire_names(api):
    schemas = api.app.openapi()["components"]["schemas"]
    assert {"oidcEnabled", "passwordAuthEnabled", "oidcMode", "lakoOrigin"} == set(
        schemas["AuthConfigResponse"]["properties"]
    )
    assert {"user", "sessionExpiresAt"} == set(schemas["AuthSessionResponse"]["properties"])
    assert {"ticket_id", "secret", "approve_url", "qr_data_uri", "expiresAt"} == set(
        schemas["QrStartResponse"]["properties"]
    )
    assert {"createdAt", "expiresAt", "ip", "userAgent", "emailConfirmationRequired", "emailHint"} == set(
        schemas["QrInfoResponse"]["properties"]
    )
