# Tích hợp vision-api — hướng dẫn cho dev backend khách

Đọc `BACKEND-CONTRACT.md` (spec chi tiết) + `API.md` (endpoint vision-api) trước. File này là
**checklist làm theo từng bước**.

`vision-api` = hàm GPU stateless: `ảnh → embedding` + FAISS index trong RAM (bản copy, dựng lại
từ backend khách). **Backend khách giữ 100% dữ liệu thật.** vision-api chỉ **ĐỌC** 2 endpoint.

---

## 0. Bạn cần cung cấp gì

| # | Việc | Bắt buộc |
|---|---|---|
| 1 | Chỗ lưu vector embedding (DB) | ✅ |
| 2 | `GET /internal/tenants` | ✅ |
| 3 | `GET /internal/tenants/{tid}/product-embeddings` (và/hoặc `/face-embeddings`) | ✅ |
| 4 | Xác thực `X-Internal-Key` cho các endpoint trên | ✅ |
| 5 | Luồng enroll: gọi `/v1/products/embed` → lưu → gọi `/admin/reload` | ✅ |
| 6 | `POST /internal/events` (nhận audit) | tùy chọn (tắt: `VISION_POST_EVENTS=false`) |
| 7 | `GET /healthz` | tùy chọn (để monitor) |

> `POST /internal/tenants/{tid}/products` và `DELETE` trong contract là **cho hệ thống enroll
> của bạn tự dùng** — vision-api KHÔNG gọi. Bạn có thể ghi thẳng vào DB.

---

## 1. Schema DB (ví dụ Postgres + pgvector)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tenants (
  id   TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE products (
  id         TEXT PRIMARY KEY,          -- product_id ổn định
  tenant_id  TEXT NOT NULL REFERENCES tenants(id),
  sku        TEXT,
  name       TEXT NOT NULL,
  -- ... metadata riêng: gia, chat_lieu, loai_da, hinh_url ...
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE product_embeddings (
  id         TEXT PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  tenant_id  TEXT NOT NULL,
  vec        vector(384) NOT NULL,      -- DINOv2-S. Đã L2-normalize (‖v‖≈1).
  image_url  TEXT,                      -- ảnh nguồn (tùy chọn)
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON product_embeddings (tenant_id);
CREATE INDEX ON product_embeddings (product_id);

-- Face (nếu dùng): persons + face_embeddings vector(512)

CREATE TABLE events (
  id         BIGSERIAL PRIMARY KEY,
  tenant_id  TEXT,
  kind       TEXT,                      -- 'face_search' | 'product_search'
  payload    JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON events (tenant_id, created_at DESC);
```

- 1 SKU **nhiều ảnh** → nhiều dòng `product_embeddings` cùng `product_id`.
- Không dùng pgvector cũng được — lưu `vec` là `bytea` / `float4[]` / JSON. vision-api tự làm
  phần search, DB chỉ cần trả lại đúng 384 số.
- Dung lượng: `50k SKU × 5 ảnh × 384 × 4B ≈ 384 MB`.

---

## 2. Endpoint phải implement (vision-api gọi tới)

Base URL bạn đặt = `VISION_BACKEND_URL`. Mọi request có header `X-Internal-Key: <secret>`
→ trả **401** nếu sai. Nên whitelist IP vision-api.

### 2.1 `GET /internal/tenants`
```json
{ "tenants": [ { "id": "t_acme", "name": "ACME Jewelry" } ] }
```
Chỉ trả tenant đang bật nhận dạng.

### 2.2 `GET /internal/tenants/{tid}/product-embeddings`
```json
{
  "tenant_id": "t_acme",
  "dim": 384,
  "model": "dinov2-small",
  "products": [
    {
      "product_id": "p_001",
      "sku": "NHAN-KC-0050",
      "name": "Nhẫn kim cương 0.5ct",
      "embeddings": [ [/* 384 số */], [/* ảnh 2 của cùng SKU */] ]
    }
  ]
}
```
- `404` nếu tenant không tồn tại.
- `products` rỗng → index rỗng, không lỗi.
- Catalog **> ~50k sp**: thêm `?cursor=<opaque>` và trả `"next": "<cursor>"` khi còn trang.
  (vision-api hiện đọc 1 lần — cần sửa nhẹ `_build()` để lặp trang; nói team vision-api.)

SQL mẫu:
```sql
SELECT p.id, p.sku, p.name,
       coalesce(json_agg(e.vec ORDER BY e.created_at) FILTER (WHERE e.id IS NOT NULL), '[]')
FROM products p
LEFT JOIN product_embeddings e ON e.product_id = p.id
WHERE p.tenant_id = $1
GROUP BY p.id;
```

### 2.3 `POST /internal/events`  *(tùy chọn)*
```json
{ "tenant_id": "t_acme", "kind": "product_search",
  "payload": { "request_id": "req_…", "match": { "product_id": "p_001", "score": 0.87 } } }
```
→ `201 { "ok": true, "id": "..." }`. Fire-and-forget — chậm/lỗi **không** được làm hỏng request.

### 2.4 `GET /healthz` *(tùy chọn)* → `200` bất kỳ khi backend sống.

---

## 3. Cấu hình vision-api (Coolify → app env vars)

```
VISION_BACKEND_URL=https://backend.cua-ban/api
VISION_BACKEND_INTERNAL_KEY=<shared secret 32B random>
VISION_ENABLE_PRODUCTS=true
VISION_ENABLE_FACE=false            # nếu chỉ làm product
PRODUCT_MATCH_THRESHOLD=0.55        # CALIBRATE trên catalog thật
VISION_POST_EVENTS=true             # false nếu chưa làm /internal/events
```
Bỏ container `mock-backend`.

---

## 4. Luồng bạn phải code

### 4.1 Thêm / sửa 1 SKU
```
1. Ảnh sản phẩm  ──POST {vision-api}/v1/products/embed──▶  { "embedding": [384 số] }
2. Lưu vào DB:  products (upsert)  +  product_embeddings (INSERT vec = embedding)
3. POST {vision-api}/admin/reload?modality=product&tenant_id={tid}
   Header: Authorization: Bearer <ADMIN_TOKEN>
```

Ví dụ (pseudo / Node):
```js
const fd = new FormData();
fd.append("file", imageBlob, "sp.jpg");
const r = await fetch(`${VISION_API}/v1/products/embed`, {
  method: "POST",
  headers: { Authorization: `Bearer ${CLIENT_TOKEN}`, "X-Tenant-ID": tenantId },
  body: fd,
});
const { embedding } = await r.json();               // 384 số, đã L2-norm

await db.query(
  `INSERT INTO product_embeddings (id, product_id, tenant_id, vec, image_url)
   VALUES ($1,$2,$3,$4,$5)`,
  [genId(), productId, tenantId, `[${embedding.join(",")}]`, imageUrl]
);

await fetch(`${VISION_API}/admin/reload?modality=product&tenant_id=${tenantId}`,
  { method: "POST", headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } });
```

### 4.2 Xoá SKU
Xoá trong DB (`ON DELETE CASCADE` xoá embeddings) → gọi `/admin/reload` như trên.

### 4.3 Nhận diện (end-user)
```
Ảnh  ──POST {vision-api}/v1/products/search──▶
  { "match": { "product_id": "p_001", "name": "...", "score": 0.87 } | null,
    "candidates": [ ... ], "threshold": 0.55 }
```
`match=null` = không có candidate nào ≥ threshold. Lấy `product_id` → join sang `products`
lấy metadata để hiển thị.

---

## 5. Token

vision-api xác thực client bằng `VISION_API_TOKENS` (JSON, set ở Coolify):
```json
{ "tok_xxx_client": {"tenant": "t_acme", "role": "client"},
  "tok_yyy_admin":  {"tenant": "*",      "role": "admin"} }
```
- `client`: gọi `/v1/*`. Nên cấp **1 token / tenant**.
- `admin`: gọi `/admin/reload`. Giữ kín, chỉ backend bạn dùng.
- Sinh token ≥ 32 byte random. Rotate định kỳ.

---

## 6. Checklist go-live

- [ ] 2 endpoint GET trả đúng shape (test bằng `curl` + `X-Internal-Key`)
- [ ] `X-Internal-Key` sai → 401; whitelist IP vision-api / private network
- [ ] Enroll 20–50 SKU thật (mỗi cái 3–5 ảnh) qua luồng 4.1
- [ ] `POST /admin/reload` → `GET {vision-api}/v1/products/index/stats` thấy đúng số
- [ ] **Calibrate threshold**: giữ 1 ảnh/SKU làm query, đo recall@1 / recall@5, chọn ngưỡng
- [ ] Đổi model sau này → **re-embed toàn bộ** (chạy lại 4.1 cho mọi ảnh), ghi đè `vec`.
      Field `dim` / `model` trong response §2.2 để phát hiện lệch.
- [ ] Backend chết lúc vision-api khởi động → vision-api vẫn `ready`, tenant lazy-load ở
      request đầu; hoặc gọi `/admin/reload` sau khi backend lên.
