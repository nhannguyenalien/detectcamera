"""Tải ảnh từ URL an toàn — chống SSRF."""
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from . import config


class UrlNotAllowed(Exception):
    pass


def _ip_is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_fetchable(url: str) -> None:
    """Ném UrlNotAllowed nếu URL không hợp lệ hoặc trỏ vào mạng nội bộ."""
    if not config.ALLOW_URL_FETCH:
        raise UrlNotAllowed("Tải ảnh theo URL đã bị tắt (VISION_ALLOW_URL_FETCH=false)")

    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise UrlNotAllowed("Chỉ chấp nhận http/https")
    host = p.hostname
    if not host:
        raise UrlNotAllowed("URL thiếu host")

    if config.URL_ALLOWLIST:
        if not any(host == d or host.endswith("." + d) for d in config.URL_ALLOWLIST):
            raise UrlNotAllowed(f"Host '{host}' không nằm trong VISION_URL_ALLOWLIST")

    # nếu host đã là IP -> check trực tiếp; nếu là tên -> resolve tất cả bản ghi
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UrlNotAllowed(f"Không resolve được host: {e}")

    ips = {info[4][0] for info in infos}
    if not ips:
        raise UrlNotAllowed("Host không có địa chỉ IP")
    bad = [ip for ip in ips if not _ip_is_public(ip)]
    if bad:
        raise UrlNotAllowed(f"Host trỏ vào IP nội bộ/không hợp lệ: {sorted(bad)}")


async def fetch_image(
    url: str, max_bytes: int, timeout: float = 15.0, max_redirects: int = 5
) -> bytes:
    """Theo redirect THỦ CÔNG, validate SSRF ở TỪNG hop trước khi kết nối. Cắt theo max_bytes."""
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
        for _ in range(max_redirects + 1):
            assert_fetchable(current)  # validate mọi hop, kể cả sau redirect
            async with c.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        raise UrlNotAllowed("Redirect thiếu Location")
                    current = str(httpx.URL(current).join(loc))
                    continue
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise UrlNotAllowed("Ảnh vượt giới hạn dung lượng")
                return bytes(buf)
    raise UrlNotAllowed("Quá nhiều redirect")
