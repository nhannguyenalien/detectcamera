# vision-api — API reference

Face detection + recognition (InsightFace SCRFD + ArcFace) trên GPU, FAISS in-RAM, multi-tenant.

- Base URL (LAN): `http://192.168.1.50:18090`
- Interactive: `GET /docs` (Swagger), `GET /redoc`
- Machine-readable: `GET /openapi.json` · `GET /` (manifest gọn cho agent)
- Repo bản tĩnh: [`openapi.json`](./openapi.json)

---

## 1. Xác thực

Mọi endpoint `/v1/*` và `/admin/*`:

| Header | Bắt buộc | Ghi chú |
|---|---|---|
| `Authorization: Bearer <token>` | ✅ | token gắn 1 tenant, hoặc token global `*` |
| `X-Tenant-ID: <tenant>` | chỉ khi token global `*` | chọn tenant thao tác |
| `X-Request-ID: <id>` | ✗ | tùy chọn, echo lại trong response; tự sinh nếu thiếu |

- Token `role=admin` mới gọi được `/admin/reload`.
- Rate limit: `120/phút/tenant` (mặc định) → HTTP `429`.
- Token khai trong biến môi trường `VISION_API_TOKENS` (JSON) — xem `.env`.

Token khai trong `.env` (KHÔNG commit). Placeholder dưới đây thay bằng token thật của bạn:

| role | token | tenant |
|---|---|---|
| client | `<CLIENT_TOKEN>` | `t_demo` |
| admin | `<ADMIN_TOKEN>` | `*` |

---

## 2. Ảnh đầu vào

`Content-Type: multipart/form-data`, một trong hai:

- `file=@path.jpg` — upload trực tiếp
- `url=https://.../a.jpg` — server tự tải về

Giới hạn `VISION_MAX_IMAGE_BYTES` (mặc định 20MB) → HTTP `413`.

---

## 3. Endpoints

### `GET /health` — liveness (no auth)
```json
{ "status": "ok", "uptime_s": 128.4 }
```

### `GET /ready` — readiness (no auth)
`200` khi model + FAISS sẵn sàng, `503` khi đang khởi động. **Poll tới `ready:true` trước khi bắn traffic.**
```json
{ "ready": true, "detail": "ready", "provider": "CUDAExecutionProvider",
  "model": "buffalo_l", "indexed": { "t_demo": { "persons": 12, "vectors": 34 } } }
```

### `GET /gpu` — thông tin GPU (no auth)
```json
{ "provider": "CUDAExecutionProvider",
  "onnxruntime_providers": ["TensorrtExecutionProvider","CUDAExecutionProvider","CPUExecutionProvider"],
  "name": "NVIDIA GeForce GTX 1650", "vram_total_mb": 4096, "vram_used_mb": 686, "gpu_util_pct": 0 }
```

### `POST /v1/faces/detect` — phát hiện mặt (client)
Form: `file` | `url`.
```json
{ "request_id": "req_f6c3…", "tenant_id": "t_demo", "count": 2,
  "faces": [ { "bbox_xyxy": [913.4,96.3,1057.1,276.2], "det_score": 0.8714 } ],
  "inference_ms": 41.2 }
```

### `POST /v1/faces/embed` — trích embedding (client)
Form: `file` | `url`. Trả vector 512-d **đã L2-normalize** cho mỗi mặt → dùng để enroll.
```json
{ "request_id": "req_e231…", "tenant_id": "t_demo", "model": "buffalo_l", "dim": 512, "count": 1,
  "faces": [ { "bbox_xyxy": [913.4,96.3,1057.1,276.2], "det_score": 0.8714,
              "embedding": [0.029962, 0.018961, /* …510 số */ ] } ],
  "inference_ms": 39.0 }
```

### `POST /v1/faces/search` — nhận dạng (client)
Form: `file` | `url`. Query: `top_k` (mặc định `5`), `threshold` (mặc định `0.40`).

Mỗi mặt: `match` = candidate top-1 **nếu** `score >= threshold`, ngược lại `null`.
`score` = cosine similarity (0..1).
```json
{ "request_id": "req_ca96…", "tenant_id": "t_demo", "count": 2, "threshold": 0.4,
  "inference_ms": 45.5, "index": { "persons": 2, "vectors": 2 },
  "faces": [
    { "bbox_xyxy": [913.4,96.3,1057.1,276.2], "det_score": 0.8714,
      "match": { "person_id": "p_73a7…", "name": "Zidane", "score": 1.0 },
      "candidates": [ { "person_id": "p_73a7…", "name": "Zidane", "score": 1.0 } ] },
    { "bbox_xyxy": [544.6,230.3,664.2,450.8], "det_score": 0.6821,
      "match": null,
      "candidates": [ { "person_id": "p_73a7…", "name": "Zidane", "score": 0.0341 } ] }
  ] }
```

### `POST /admin/reload` — rebuild FAISS (admin)
Query: `tenant_id` (bỏ trống = tất cả tenant).
```json
{ "reloaded": ["t_demo"], "stats": { "t_demo": { "persons": 2, "vectors": 2 } } }
```

### `GET /v1/index/stats` — thống kê index (client / admin)
```json
{ "stats": { "t_demo": { "persons": 2, "vectors": 2 } } }
```

### `GET /` — manifest cho agent (no auth)
Trả JSON liệt kê endpoint + cách auth + `enroll_flow` để client/AI agent tự khám phá.

---

## 4. Mã lỗi

| HTTP | Khi nào |
|---|---|
| `400` | token global thiếu `X-Tenant-ID` (ở endpoint theo tenant) |
| `401` | thiếu / sai `Authorization` |
| `403` | `X-Tenant-ID` không khớp token, hoặc gọi `/admin/*` bằng token không phải admin |
| `413` | ảnh vượt giới hạn dung lượng |
| `422` | thiếu cả `file` lẫn `url`, hoặc tham số sai |
| `429` | vượt rate limit tenant |
| `503` | service chưa `ready` |

Body lỗi: `{ "detail": "<mô tả>" }`.

---

## 5. Luồng enroll 1 người

```
1) POST /v1/faces/embed            (ảnh chân dung)     -> faces[0].embedding  (512-d)
2) POST {BACKEND}/internal/tenants/{tid}/persons
   Body: { "name": "Nguyen Van A", "embeddings": [ <embedding> ] }
   Header: X-Internal-Key: <INTERNAL_KEY>
3) POST /admin/reload?tenant_id={tid}                  (token admin)
4) POST /v1/faces/search           (ảnh cần kiểm tra)  -> faces[i].match.person_id
```

`scripts/e2e-test.sh` chạy đúng 4 bước này với ảnh mẫu.

---

## 6. Ví dụ

### curl
```bash
API=http://192.168.1.50:18090
TOKEN=<CLIENT_TOKEN>
H=(-H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: t_demo")

curl "${H[@]}" -F file=@photo.jpg "$API/v1/faces/detect"
curl "${H[@]}" -F file=@photo.jpg "$API/v1/faces/embed"
curl "${H[@]}" -F url=https://example.com/x.jpg "$API/v1/faces/search?top_k=5&threshold=0.4"
```

### Python
```python
import requests

API = "http://192.168.1.50:18090"
S = requests.Session()
S.headers.update({
    "Authorization": "Bearer <CLIENT_TOKEN>",
    "X-Tenant-ID": "t_demo",
})

with open("photo.jpg", "rb") as f:
    r = S.post(f"{API}/v1/faces/search", files={"file": f}, params={"threshold": 0.4})
r.raise_for_status()
for face in r.json()["faces"]:
    m = face["match"]
    print(face["bbox_xyxy"], "->", (m["name"], m["score"]) if m else "unknown")
```

### AI agent (tool spec gợi ý)
```json
{
  "name": "face_search",
  "description": "Nhận dạng người trong ảnh. Trả bbox + person_id + score cho từng khuôn mặt.",
  "http": { "method": "POST", "url": "http://192.168.1.50:18090/v1/faces/search",
            "headers": { "Authorization": "Bearer <CLIENT_TOKEN>", "X-Tenant-ID": "<TENANT>" },
            "multipart": { "file": "<binary>" }, "query": { "top_k": 5, "threshold": 0.4 } },
  "returns": "faces[].match.{person_id,name,score} | null"
}
```
Agent nên: gọi `GET /` lấy manifest → `GET /ready` đợi `ready:true` → gọi endpoint.

---

## 7. Backend contract (`mock-backend`, thay bằng backend thật sau)

`X-Internal-Key: <INTERNAL_KEY>` cho tất cả.

| Method | Path | Việc |
|---|---|---|
| GET | `/internal/tenants` | `{ "tenants": [ {id,name} ] }` |
| GET | `/internal/tenants/{tid}/face-embeddings` | `{ tenant_id, dim, model, persons:[{person_id,name,embeddings:[[…]]}] }` |
| POST | `/internal/tenants/{tid}/persons` | body `{name, embeddings:[[…]], person_id?}` → tạo/ghi thêm |
| DELETE | `/internal/tenants/{tid}/persons/{pid}` | xoá |
| POST | `/internal/events` | vision-api ghi audit `{tenant_id, kind, payload}` |
| GET | `/internal/events?tenant_id=&limit=` | đọc audit (debug) |
| GET | `/healthz` | – |

Khi có backend thật: implement 7 endpoint trên, set `VISION_BACKEND_URL` + `VISION_BACKEND_INTERNAL_KEY`, bỏ container `mock-backend`.
