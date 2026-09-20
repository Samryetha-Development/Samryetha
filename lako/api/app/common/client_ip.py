"""Resolve client IPs without trusting caller-controlled forwarding headers."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from fastapi import Request

from app.common.config import get_settings


def _address(value: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """Return the first untrusted hop when the direct peer is a trusted proxy."""
    peer = request.client.host if request.client else None
    if peer is None:
        return None
    peer_address = _address(peer)
    if peer_address is None:
        return peer

    networks = []
    for raw in get_settings().trusted_proxy_cidrs.split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            continue
    if not any(peer_address in network for network in networks):
        return str(peer_address)

    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    hops.append(str(peer_address))
    for raw in reversed(hops):
        address = _address(raw)
        if address is None:
            continue
        if any(address in network for network in networks):
            continue
        return str(address)
    return str(peer_address)
