"""Auth (Bearer + X-Tenant-ID) + rate limit theo tenant + request-id."""
import asyncio
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config, tokens

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="API token cấp cho 1 tenant, hoặc token global (role=admin, tenant='*').",
)


@dataclass
class Auth:
    token: str
    tenant: str
    role: str
    request_id: str


async def auth_ctx(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    x_tenant_id: str = Header(default="", description="Bắt buộc khi token là global '*'."),
    x_request_id: str = Header(default="", description="Tùy chọn; echo lại trong response."),
) -> Auth:
    if creds is None or not creds.credentials:
        raise HTTPException(401, "Thiếu header 'Authorization: Bearer <token>'")
    token = creds.credentials

    meta = tokens.lookup(token)
    if not meta:
        raise HTTPException(401, "Token không hợp lệ")

    role = meta.get("role", "client")
    bound = meta.get("tenant", "*")
    tenant = x_tenant_id.strip()

    if bound == "*":
        # token global: tenant lấy từ header, có thể rỗng ở endpoint admin
        pass
    else:
        if tenant and tenant != bound:
            raise HTTPException(403, "X-Tenant-ID không khớp token")
        tenant = bound

    request_id = x_request_id.strip() or f"req_{uuid.uuid4().hex[:16]}"
    return Auth(token=token, tenant=tenant, role=role, request_id=request_id)


def require_tenant(auth: Auth) -> str:
    """Endpoint thao tác theo tenant người gọi -> bắt buộc có tenant."""
    if not auth.tenant:
        raise HTTPException(400, "Token global cần header 'X-Tenant-ID'")
    return auth.tenant


class RateLimiter:
    """Sliding-window đơn giản, in-process (không cần Redis ở quy mô LAN)."""

    def __init__(self, per_min: int) -> None:
        self.per_min = per_min
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        if self.per_min <= 0:
            return
        now = time.time()
        async with self._lock:
            dq = self._hits[key]
            while dq and dq[0] < now - 60:
                dq.popleft()
            if len(dq) >= self.per_min:
                raise HTTPException(
                    429, f"Vượt rate limit ({self.per_min}/phút) cho tenant '{key}'"
                )
            dq.append(now)
