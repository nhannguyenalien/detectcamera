"""FAISS index trong RAM, 1 index / tenant. Cosine = inner-product trên vector đã chuẩn hoá."""
import asyncio

import faiss
import numpy as np

from . import config


class TenantIndex:
    def __init__(self, dim: int = config.EMB_DIM) -> None:
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim)
        self.person_ids: list[str] = []      # row -> person_id
        self.names: dict[str, str] = {}      # person_id -> name
        self.counts: dict[str, int] = {}     # person_id -> số embedding

    def add_person(self, person_id: str, name: str | None, embeddings) -> None:
        arr = np.asarray(embeddings, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != self.dim:
            raise ValueError(f"embedding sai shape {arr.shape}, cần (N,{self.dim})")
        faiss.normalize_L2(arr)
        self.index.add(arr)
        self.person_ids.extend([person_id] * arr.shape[0])
        self.names[person_id] = name or person_id
        self.counts[person_id] = self.counts.get(person_id, 0) + arr.shape[0]

    def search(self, vec, top_k: int) -> list[dict]:
        if self.index.ntotal == 0:
            return []
        q = np.asarray([vec], dtype=np.float32)
        faiss.normalize_L2(q)
        k = min(max(top_k * 3, top_k), self.index.ntotal)
        scores, idxs = self.index.search(q, k)
        best: dict[str, dict] = {}
        for score, row in zip(scores[0], idxs[0]):
            if row < 0:
                continue
            pid = self.person_ids[row]
            s = round(float(score), 4)
            if pid not in best or s > best[pid]["score"]:
                best[pid] = {"person_id": pid, "name": self.names.get(pid), "score": s}
        return sorted(best.values(), key=lambda x: -x["score"])[:top_k]

    @property
    def n_vectors(self) -> int:
        return self.index.ntotal

    @property
    def n_persons(self) -> int:
        return len(self.names)


class IndexStore:
    """Generic per-tenant FAISS store. Dùng cho cả face lẫn product — chỉ khác
    hàm fetch ở backend + tên field id trong payload."""

    def __init__(
        self,
        backend,
        fetch_attr: str = "get_face_embeddings",
        items_key: str = "persons",
        id_key: str = "person_id",
        default_dim: int = config.EMB_DIM,
    ) -> None:
        self.backend = backend
        self._fetch_attr = fetch_attr
        self._items_key = items_key
        self._id_key = id_key
        self._default_dim = default_dim
        self.tenants: dict[str, TenantIndex] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._glock = asyncio.Lock()

    async def _lock_for(self, tid: str) -> asyncio.Lock:
        async with self._glock:
            return self._locks.setdefault(tid, asyncio.Lock())

    async def _build(self, tid: str) -> TenantIndex:
        data = await getattr(self.backend, self._fetch_attr)(tid)
        idx = TenantIndex(dim=int(data.get("dim", self._default_dim)))
        for it in data.get(self._items_key, []):
            embs = it.get("embeddings") or []
            if embs:
                idx.add_person(it[self._id_key], it.get("name"), embs)
        return idx

    async def ensure(self, tid: str) -> TenantIndex:
        if tid in self.tenants:
            return self.tenants[tid]
        lock = await self._lock_for(tid)
        async with lock:
            if tid not in self.tenants:
                self.tenants[tid] = await self._build(tid)
            return self.tenants[tid]

    async def reload(self, tid: str) -> TenantIndex:
        lock = await self._lock_for(tid)
        async with lock:
            self.tenants[tid] = await self._build(tid)
            return self.tenants[tid]

    async def reload_all(self) -> list[str]:
        tenants = await self.backend.get_tenants()
        for t in tenants:
            await self.reload(t["id"])
        return [t["id"] for t in tenants]

    def stats(self, tid: str | None = None) -> dict:
        items = self.tenants.items() if tid is None else [(tid, self.tenants.get(tid))]
        return {
            t: {"persons": i.n_persons, "vectors": i.n_vectors}
            for t, i in items
            if i is not None
        }
