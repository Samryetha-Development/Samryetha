from types import SimpleNamespace

from starlette.requests import Request

from app.common import client_ip as client_ip_module


def _request(peer: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("testserver", 80),
        }
    )


def test_untrusted_peer_cannot_spoof_forwarded_for(monkeypatch):
    monkeypatch.setattr(
        client_ip_module,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs="172.16.0.0/12"),
    )

    assert client_ip_module.client_ip(_request("203.0.113.8", "198.51.100.4")) == "203.0.113.8"


def test_trusted_proxy_chain_returns_first_untrusted_hop(monkeypatch):
    monkeypatch.setattr(
        client_ip_module,
        "get_settings",
        lambda: SimpleNamespace(trusted_proxy_cidrs="127.0.0.1/32,172.16.0.0/12"),
    )

    request = _request("172.19.0.4", "198.51.100.9, 172.19.0.1")
    assert client_ip_module.client_ip(request) == "198.51.100.9"
