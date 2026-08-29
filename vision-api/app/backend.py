"""Client tới backend "source of truth" (mock hoặc thật)."""
import httpx

from . import config


class BackendClient:
    def __init__(self) -> None:
        self._c = httpx.AsyncClient(
            base_url=config.BACKEND_URL,
            timeout=config.BACKEND_TIMEOUT,
            headers={"X-Internal-Key": config.BACKEND_INTERNAL_KEY},
        )

    async def close(self) -> None:
        await self._c.aclose()

    async def get_tenants(self) -> list[dict]:
        r = await self._c.get("/internal/tenants")
        r.raise_for_status()
        return r.json().get("tenants", [])

    async def get_face_embeddings(self, tenant_id: str) -> dict:
        r = await self._c.get(f"/internal/tenants/{tenant_id}/face-embeddings")
        r.raise_for_status()
        return r.json()

    async def post_event(self, tenant_id: str, kind: str, payload: dict) -> None:
        try:
            await self._c.post(
                "/internal/events",
                json={"tenant_id": tenant_id, "kind": kind, "payload": payload},
            )
        except Exception as e:  # noqa: BLE001 - audit best-effort, không chặn request
            print(f"[backend] post_event failed: {e}")
