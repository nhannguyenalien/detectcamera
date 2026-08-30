"""Token API: merge env tĩnh (config.API_TOKENS) + động từ backend (/internal/api-tokens)."""
import asyncio

from . import config

_dynamic: dict = {}


def lookup(token: str):
    return config.API_TOKENS.get(token) or _dynamic.get(token)


def count() -> int:
    return len(set(config.API_TOKENS) | set(_dynamic))


async def refresh_once(backend) -> int:
    global _dynamic
    data = await backend.get_api_tokens()
    _dynamic = data.get("tokens", {}) or {}
    return len(_dynamic)


async def refresher(backend):
    while True:
        await asyncio.sleep(config.TOKENS_REFRESH_SEC)
        try:
            n = await refresh_once(backend)
            print(f"[tokens] refreshed: {n} dynamic")
        except Exception as e:  # noqa: BLE001
            print(f"[tokens] refresh failed: {e}")
