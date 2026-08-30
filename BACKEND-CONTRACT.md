# Backend contract — cái mà `vision-api` cần từ "source of truth"

`vision-api` không lưu người/embedding. Nó **đọc** từ backend lúc khởi động và khi `/admin/reload`,
và **ghi** audit event. `mock-backend` trong repo là bản giả implement đúng contract này —
backend thật chỉ cần trả đúng shape thì thay 1-1.

Cấu hình phía vision-api:
```
VISION_BACKEND_URL=https://backend.noi-bo/api        # base URL
VISION_BACKEND_INTERNAL_KEY=<shared secret>          # gửi ở header X-Internal-Key
VISION_BACKEND_TIMEOUT=20
```

Mọi request từ vision-api kèm header: `X-Internal-Key: <VISION_BACKEND_INTERNAL_KEY>`.
Backend phải từ chối (401) nếu sai. Nên whitelist IP vision-api thêm.

---

## Kiểu dữ liệu chung

- **embedding**: mảng `float32` **đúng 512 phần tử**, **đã L2-normalize** (‖v‖₂ = 1).
  Sinh bởi `POST /v1/faces/embed` (model `buffalo_l` / ArcFace `w600k_r50`).
  Nếu đổi model → `dim` và ý nghĩa đổi theo; field `model` để phát hiện lệch.
- **person_id / tenant_id**: string ổn định, do backend cấp.
- thời gian: ISO-8601 UTC.

---

## 1. `GET /internal/tenants`

Danh sách tenant vision-api cần build index lúc khởi động.

**200**
```json
{ "tenants": [ { "id": "t_acme", "name": "ACME Corp" },
               { "id": "t_demo", "name": "Demo" } ] }
```
Chỉ trả tenant đang bật dịch vụ nhận dạng. Có thể phân trang nếu nhiều — vision-api hiện đọc 1 lần,
nếu phân trang thì thêm `?page=` và trả `next`.

---

## 2. `GET /internal/tenants/{tid}/face-embeddings`

Toàn bộ người + embedding của 1 tenant. Đây là dữ liệu để dựng FAISS.

**200**
```json
{
  "tenant_id": "t_acme",
  "dim": 512,
  "model": "buffalo_l/arcface_r50",
  "persons": [
    { "person_id": "p_001", "name": "Nguyen Van A",
      "embeddings": [ [0.01, -0.02, /* …510 số, tổng 512 */ ],
                      [0.03,  0.00, /* … */ ] ] },
    { "person_id": "p_002", "name": "Tran Thi B",
      "embeddings": [ [ /* 512 số */ ] ] }
  ]
}
```
- 1 người **nhiều** embedding (nhiều ảnh enroll) → search lấy max cosine trong các vector của người đó.
- `embeddings` rỗng → người đó bị bỏ qua khi dựng index.
- **404** nếu tenant không tồn tại.
- Với tenant lớn (>50k người) nên hỗ trợ `?cursor=` để stream; vision-api sẽ cần sửa `_build()` để phân trang.

---

## 3. `POST /internal/tenants/{tid}/persons`

Enroll / cập nhật 1 người. Client gọi sau khi lấy embedding từ `POST /v1/faces/embed`.
(vision-api KHÔNG tự gọi cái này — đây là để hệ thống enroll của bạn dùng.)

**Request**
```json
{ "name": "Nguyen Van A",
  "embeddings": [ [ /* 512 số, L2-normed */ ] ],
  "person_id": "p_001"   // tùy chọn; có = update/append, không = tạo mới
}
```

**201**
```json
{ "person_id": "p_001", "name": "Nguyen Van A", "embedding_count": 3 }
```
Backend nên: validate độ dài 512, chuẩn hoá lại nếu cần, chống trùng ảnh (optional).
Sau khi enroll xong gọi `POST {vision-api}/admin/reload?tenant_id={tid}`.

---

## 4. `DELETE /internal/tenants/{tid}/persons/{pid}`

Xoá người + toàn bộ embedding (right-to-be-forgotten).

**200** `{ "deleted": 1 }`  ·  **200** `{ "deleted": 0 }` nếu không có.

Sau khi xoá gọi `/admin/reload`.

---

## 4b. Product visual search (modality `product`, 1 ảnh = 1 sp)

Song song với face — cùng shape, chỉ khác: **dim 384** (DINOv2-S), key `product_id`, có thêm `sku`,
**không có bbox** (embed cả ảnh). vision-api gọi khi `VISION_ENABLE_PRODUCTS=true`.

### `GET /internal/tenants/{tid}/product-embeddings`
```json
{
  "tenant_id": "t_acme",
  "dim": 384,
  "model": "dinov2-small",
  "products": [
    { "product_id": "p_001", "sku": "SKU-COCA-330", "name": "Coca 330ml lon",
      "embeddings": [ [ /* 384 số, L2-normed */ ], [ /* ảnh thứ 2 của cùng sp */ ] ] }
  ]
}
```
- Nhiều ảnh / 1 sp → search lấy max cosine. `embeddings` rỗng → sp bị bỏ qua.
- **404** nếu tenant không tồn tại.

### `POST /internal/tenants/{tid}/products`
```json
{ "name": "Coca 330ml lon", "sku": "SKU-COCA-330",
  "embeddings": [ [ /* 384 số */ ] ], "product_id": "p_001" }
```
→ **201** `{ "product_id": "p_001", "name": "...", "sku": "...", "embedding_count": 2 }`

### `DELETE /internal/tenants/{tid}/products/{pid}`
→ **200** `{ "deleted": 1 }`

Sau enroll/xoá gọi `POST {vision-api}/admin/reload?modality=product&tenant_id={tid}`.

---

## 5. `POST /internal/events`

vision-api ghi audit mỗi lần `search` (best-effort, fire-and-forget — backend chậm/lỗi
không được làm hỏng request nhận dạng).

**Request**
```json
{ "tenant_id": "t_acme", "kind": "face_search",
  "payload": { "request_id": "req_…", "n_faces": 2,
               "matches": [ { "person_id": "p_001", "name": "…", "score": 0.71 } ] } }
```
**201** `{ "ok": true, "id": "ev_…" }`

Backend nên gắn thêm: thời gian nhận, IP/nguồn gọi, token id (không log token). Dùng cho audit trail
sinh trắc học (yêu cầu pháp lý ở nhiều nơi).

---

## 5b. `GET /internal/api-tokens`  *(tùy chọn — nếu muốn quản token động)*

Cho `vision-api` xác thực client bằng token lưu ở backend thay vì chỉ env tĩnh.
Bật phía vision-api: `VISION_TOKENS_FROM_BACKEND=true` (refresh mỗi `VISION_TOKENS_REFRESH_SEC`, mặc định 60s).

```json
{ "tokens": {
    "tok_ab12...": { "tenant": "t_acme", "role": "client" },
    "tok_root...": { "tenant": "*",       "role": "admin"  }
} }
```
vision-api **merge** map này với `VISION_API_TOKENS` (env). Rotate/revoke ở backend → có hiệu lực sau ≤ refresh interval.

---

## 6. `GET /internal/events?tenant_id=&limit=`  *(tùy chọn, để debug)*

vision-api không gọi. `mock-backend` có sẵn để kiểm tra.

---

## 7. `GET /healthz`

**200** bất kỳ khi backend sống. vision-api không strict cái này nhưng nên có để monitor.

---

## Ghi chú tích hợp

| Vấn đề | Xử lý |
|---|---|
| Backend chưa sẵn lúc vision-api khởi động | vision-api vẫn `ready`, tenant sẽ **lazy build** ở request đầu; hoặc gọi `/admin/reload` sau |
| Thêm/xoá người | enroll/delete ở backend → `POST /admin/reload?tenant_id=` (chưa có webhook; có thể thêm) |
| Nhiều vision-api instance | mỗi instance giữ FAISS riêng trong RAM → gọi `/admin/reload` lên **từng** instance, hoặc để TTL reload |
| Đổi model nhận dạng | phải re-embed toàn bộ (embedding cũ không so được với model mới); `dim`/`model` giúp phát hiện |
| Bảo mật kênh nội bộ | `X-Internal-Key` + whitelist IP + TLS. Không để endpoint `/internal/*` ra Internet |
