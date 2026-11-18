from __future__ import annotations

from fastapi import Request

from app.core.config import settings
from app.utils.request_meta import get_request_meta


def make_request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": ("192.0.2.10", 1234),
            "server": ("testserver", 80),
        }
    )


def test_request_meta_uses_client_ip_when_proxy_headers_are_untrusted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    request = make_request(
        [
            (b"x-forwarded-for", b"198.51.100.4"),
            (b"user-agent", b"test-agent"),
        ]
    )

    meta = get_request_meta(request)

    assert meta.ip_address == "192.0.2.10"
    assert meta.user_agent == "test-agent"


def test_request_meta_uses_first_valid_forwarded_ip_when_trusted(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    request = make_request(
        [
            (b"x-forwarded-for", b"198.51.100.4, 192.0.2.20"),
            (b"user-agent", b"x" * 600),
        ]
    )

    meta = get_request_meta(request)

    assert meta.ip_address == "198.51.100.4"
    assert meta.user_agent == "x" * 512


def test_request_meta_falls_back_when_forwarded_ip_is_invalid(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    request = make_request([(b"x-forwarded-for", b"not-an-ip")])

    assert get_request_meta(request).ip_address == "192.0.2.10"
