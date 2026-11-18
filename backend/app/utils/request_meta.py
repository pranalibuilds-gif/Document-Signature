from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from fastapi import Request

from app.core.config import settings


@dataclass(frozen=True)
class RequestMeta:
    ip_address: str | None
    user_agent: str | None


def get_request_meta(request: Request) -> RequestMeta:
    ip_address: str | None = request.client.host if request.client else None
    if settings.trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            candidate = forwarded_for.split(",", maxsplit=1)[0].strip()
            try:
                ip_address = str(ipaddress.ip_address(candidate))
            except ValueError:
                pass

    user_agent = request.headers.get("user-agent")
    return RequestMeta(
        ip_address=ip_address,
        user_agent=user_agent[:512] if user_agent else None,
    )
