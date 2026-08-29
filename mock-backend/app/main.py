"""
Mock backend = "source of truth" giả lập cho vision-api.

KHÔNG phải backend thật. Chỉ để vision-api có chỗ sync embeddings/persons/events
theo đúng contract. Khi có backend thật, chỉ cần trỏ VISION_BACKEND_URL sang đó
và implement 4 endpoint /internal/* dưới đây.

Auth nội bộ: header  X-Internal-Key: <INTERNAL_KEY>
"""
import json
import os
import sqlite3
import struct
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DB_PATH", "/data/backend/backend.db")
INTERNAL_KEY = os.getenv("INTERNAL_KEY", "dev-internal-key")
EMB_DIM = int(os.getenv("EMB_DIM", "512"))
EMB_MODEL = os.getenv("EMB_MODEL", "buffalo_l/arcface_r50")
SEED_TENANTS = [t.strip() for t in os.getenv("SEED_TENANTS", "t_demo").split(",") if t.strip()]

_lock = threading.Lock()
_conn: sqlite3.Connection = None  # type: ignore

app = FastAPI(title="Mock Backend (vision source-of-truth)", version="1.0.0")


def db() -> sqlite3.Connection:
    return _conn


def _init_db() -> None:
    global _conn
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS persons (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embeddings (
            id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            vec BLOB NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            kind TEXT,
            payload TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_persons_tenant ON persons(tenant_id);
        CREATE INDEX IF NOT EXISTS ix_emb_person ON embeddings(person_id);
        CREATE INDEX IF NOT EXISTS ix_events_tenant ON events(tenant_id);
        """
    )
    now = time.time()
    for tid in SEED_TENANTS:
        _conn.execute(
            "INSERT OR IGNORE INTO tenants(id,name,created_at) VALUES (?,?,?)",
            (tid, tid.replace("t_", "").title() or tid, now),
        )
    _conn.commit()


def _pack(vec: List[float]) -> bytes:
    if len(vec) != EMB_DIM:
        raise HTTPException(422, f"embedding phải có {EMB_DIM} chiều, nhận {len(vec)}")
    return struct.pack(f"<{EMB_DIM}f", *[float(x) for x in vec])


def _unpack(blob: bytes) -> List[float]:
    return list(struct.unpack(f"<{EMB_DIM}f", blob))


def require_internal(x_internal_key: str = Header(default="")) -> None:
    if x_internal_key != INTERNAL_KEY:
        raise HTTPException(401, "X-Internal-Key sai hoặc thiếu")


@app.on_event("startup")
def _startup() -> None:
    _init_db()


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"status": "ok", "db": DB_PATH}


# ----------------------------- contract /internal ----------------------------- #

@app.get("/internal/tenants", dependencies=[Depends(require_internal)])
def list_tenants() -> Dict[str, Any]:
    rows = db().execute("SELECT id,name FROM tenants ORDER BY created_at").fetchall()
    return {"tenants": [{"id": r[0], "name": r[1]} for r in rows]}


@app.get(
    "/internal/tenants/{tid}/face-embeddings",
    dependencies=[Depends(require_internal)],
)
def face_embeddings(tid: str = Path(...)) -> Dict[str, Any]:
    t = db().execute("SELECT id FROM tenants WHERE id=?", (tid,)).fetchone()
    if not t:
        raise HTTPException(404, "tenant không tồn tại")
    prows = db().execute(
        "SELECT id,name FROM persons WHERE tenant_id=? ORDER BY created_at", (tid,)
    ).fetchall()
    persons = []
    for pid, name in prows:
        erows = db().execute(
            "SELECT vec FROM embeddings WHERE person_id=? ORDER BY created_at", (pid,)
        ).fetchall()
        persons.append(
            {
                "person_id": pid,
                "name": name,
                "embeddings": [_unpack(e[0]) for e in erows],
            }
        )
    return {"tenant_id": tid, "dim": EMB_DIM, "model": EMB_MODEL, "persons": persons}


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    embeddings: List[List[float]] = Field(min_length=1)
    person_id: Optional[str] = None


@app.post(
    "/internal/tenants/{tid}/persons",
    dependencies=[Depends(require_internal)],
    status_code=201,
)
def upsert_person(body: PersonIn, tid: str = Path(...)) -> Dict[str, Any]:
    with _lock:
        if not db().execute("SELECT 1 FROM tenants WHERE id=?", (tid,)).fetchone():
            db().execute(
                "INSERT INTO tenants(id,name,created_at) VALUES (?,?,?)",
                (tid, tid, time.time()),
            )
        pid = body.person_id or f"p_{uuid.uuid4().hex[:12]}"
        now = time.time()
        exists = db().execute("SELECT 1 FROM persons WHERE id=?", (pid,)).fetchone()
        if exists:
            db().execute("UPDATE persons SET name=? WHERE id=?", (body.name, pid))
        else:
            db().execute(
                "INSERT INTO persons(id,tenant_id,name,created_at) VALUES (?,?,?,?)",
                (pid, tid, body.name, now),
            )
        for vec in body.embeddings:
            db().execute(
                "INSERT INTO embeddings(id,person_id,tenant_id,vec,created_at) VALUES (?,?,?,?,?)",
                (f"e_{uuid.uuid4().hex[:12]}", pid, tid, _pack(vec), now),
            )
        db().commit()
        cnt = db().execute(
            "SELECT COUNT(*) FROM embeddings WHERE person_id=?", (pid,)
        ).fetchone()[0]
    return {"person_id": pid, "name": body.name, "embedding_count": cnt}


@app.delete(
    "/internal/tenants/{tid}/persons/{pid}",
    dependencies=[Depends(require_internal)],
)
def delete_person(tid: str, pid: str) -> Dict[str, Any]:
    with _lock:
        db().execute("DELETE FROM embeddings WHERE person_id=? AND tenant_id=?", (pid, tid))
        cur = db().execute("DELETE FROM persons WHERE id=? AND tenant_id=?", (pid, tid))
        db().commit()
    return {"deleted": cur.rowcount}


class EventIn(BaseModel):
    tenant_id: Optional[str] = None
    kind: str = "recognition"
    payload: Dict[str, Any] = {}


@app.post("/internal/events", dependencies=[Depends(require_internal)], status_code=201)
def post_event(ev: EventIn) -> Dict[str, Any]:
    eid = f"ev_{uuid.uuid4().hex[:12]}"
    with _lock:
        db().execute(
            "INSERT INTO events(id,tenant_id,kind,payload,created_at) VALUES (?,?,?,?,?)",
            (eid, ev.tenant_id, ev.kind, json.dumps(ev.payload)[:100_000], time.time()),
        )
        db().commit()
    return {"ok": True, "id": eid}


@app.get("/internal/events", dependencies=[Depends(require_internal)])
def list_events(tenant_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    limit = max(1, min(limit, 500))
    if tenant_id:
        rows = db().execute(
            "SELECT id,tenant_id,kind,payload,created_at FROM events WHERE tenant_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT id,tenant_id,kind,payload,created_at FROM events "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "events": [
            {
                "id": r[0],
                "tenant_id": r[1],
                "kind": r[2],
                "payload": json.loads(r[3] or "{}"),
                "created_at": r[4],
            }
            for r in rows
        ]
    }
