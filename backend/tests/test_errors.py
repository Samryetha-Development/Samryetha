"""错误包络：{error:{code,message,requestId,details?}} — 复刻 Fastify error handler。"""


def _envelope(res):
    return res.json()["error"]


def test_api_error_envelope(client):
    res = client.get("/demo/bad")
    assert res.status_code == 400
    err = _envelope(res)
    assert err["code"] == "BAD_REQUEST"
    assert err["message"] == "nope"
    assert err["requestId"].startswith("req_")
    assert "details" not in err


def test_not_found_code(client):
    res = client.get("/demo/notfound")
    assert res.status_code == 404
    assert _envelope(res)["code"] == "NOT_FOUND"


def test_unmatched_path_envelope(client):
    # 未匹配路径：Starlette 路由自己抛的 404 也要进包络，不能是 {"detail": "Not Found"}
    res = client.get("/demo/no-such-route")
    assert res.status_code == 404
    err = _envelope(res)
    assert err["code"] == "NOT_FOUND"
    assert err["requestId"].startswith("req_")
    assert "detail" not in res.json()


def test_method_not_allowed_envelope(client):
    # 路径存在但方法不匹配：405 进包络，并保留 Starlette 的 Allow 头（协议约定）
    res = client.post("/demo/bad")
    assert res.status_code == 405
    err = _envelope(res)
    assert err["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in res.headers["allow"]


def test_validation_error_422(client):
    # 缺必填字段 → 422 VALIDATION_ERROR，details 形状 {field,message,code}
    res = client.post("/demo/echo", json={})
    assert res.status_code == 422
    err = _envelope(res)
    assert err["code"] == "VALIDATION_ERROR"
    # 校验详情（PR #5）后 message 变为 "Validation failed — {field}: {msg}"，此处只断言前缀
    # After validation-detail (PR #5), message becomes "Validation failed — {field}: {msg}"; assert the prefix only
    assert err["message"].startswith("Validation failed")
    d = err["details"][0]
    assert d["field"] == "name"  # 剥掉了 body 前缀
    assert "code" in d


def test_validation_extra_field_stripped(client):
    # zod strip 语义：多余字段静默丢弃，不报错
    res = client.post("/demo/echo", json={"name": "ok", "junk": 1})
    assert res.status_code == 200
    assert res.json() == {"name": "ok"}


def test_empty_json_body_400(client):
    # 声明了 body 模型但 JSON body 为空 → 400 BAD_REQUEST（FST_ERR_CTP_EMPTY_JSON_BODY）
    res = client.post("/demo/echo", content=b"", headers={"content-type": "application/json"})
    assert res.status_code == 400
    err = _envelope(res)
    assert err["code"] == "BAD_REQUEST"
    assert err["message"] == "Request body must not be empty"


def test_no_body_post_without_content_type_allowed(client):
    # bodyless 路由不要求 content-type（logout 之类）
    res = client.get("/demo/bad")
    assert res.status_code == 400
