# vision-dashboard

Dashboard quản lý vision-api + **backend `/internal/*` thật** (thay `mock-backend`).
Cloudflare Pages + Pages Functions + Neon (Postgres/pgvector). Ảnh chỉ dùng để embed rồi bỏ.

```
Cloudflare Pages
 ├─ public/              Tabler UI (admin + portal client)
 ├─ functions/api/*      dashboard API   ── sau Cloudflare Access
 └─ functions/internal/* backend cho vision-api gọi ── sau X-Internal-Key (exclude khỏi Access)
        │
        ▼  Neon (tenants, api_tokens, products+product_embeddings,
                 persons + face_embeddings(512) + body_embeddings(256), events)
        ▲
        └─ enroll SP:     /v1/products/embed        → lưu vec → /admin/reload?modality=product
        └─ enroll người:  /v1/faces/embed + /v1/body/embed → lưu → /admin/reload?modality=face,body
```

## Đăng nhập

2 cách (dùng cách nào cũng được, có thể bật cả 2):

1. **Email + password** (bảng `users`, session cookie ký bằng `SESSION_SECRET`).
   Form login ngay trên `/`. Admin tạo user client ở tab **Clients** (điền mật khẩu) hoặc `POST /api/users`.
2. **Cloudflare Access** — nếu bật, header `Cf-Access-Authenticated-User-Email` được chấp nhận luôn.
   Nhớ **exclude `/internal/*`** khỏi Access (để vision-api gọi được).

Role: email trong `ADMIN_EMAILS` (hoặc `users.role='admin'`) = admin; còn lại = client (chỉ tenant `owner_email` của mình).

## Vai trò

| | admin (email trong `ADMIN_EMAILS`) | client (email = `tenants.owner_email`) |
|---|---|---|
| Sản phẩm CRUD + enroll | mọi tenant | tenant của mình |
| **Người** (enroll face+body, xoá) | mọi tenant | tenant của mình |
| Test nhận diện (SP + Người) | ✓ | ✓ (tenant mình) |
| Clients (tạo tenant) | ✓ | – |
| Token & Usage | mọi tenant | tenant mình (xem + rotate token) |
| Events | ✓ | tenant mình |

## Deploy

### 1. Neon
```bash
psql "$DATABASE_URL" -f schema.sql        # tạo bảng + extension vector + tenant t_demo
```
Dùng **pooled** connection string (`...-pooler...`).

### 2. Cloudflare Pages
```bash
npm i
npx wrangler pages project create vision-dashboard
npx wrangler pages deploy public          # hoặc nối GitHub repo, build output = public/
```
Set **Environment variables** (Pages → Settings):
```
DATABASE_URL        = postgresql://...-pooler...neon.tech/neondb?sslmode=require
INTERNAL_KEY        = <shared secret, 32B random>
VISION_API_URL      = https://vision-api.schoolsai.work
VISION_CLIENT_TOKEN = <token client bootstrap>   # để dashboard gọi /v1/products/embed|search
VISION_ADMIN_TOKEN  = <token admin>              # để gọi /admin/reload
DEFAULT_TENANT      = t_demo
ADMIN_EMAILS        = ban@example.com,teammate@example.com
```

### 3. Cloudflare Access (auth cho `/api/*` và UI)
Zero Trust → Access → Applications → **Add self-hosted**:
- Domain: `<project>.pages.dev` (hoặc custom domain), Path: `/`
- **Exclude path**: thêm `/internal/*` (để vision-api gọi được, chỉ chắn bằng `X-Internal-Key`)
- Policy: Allow — emails / group của bạn (admin + client). Client cũng phải được Access cho vào.

### 4. Trỏ vision-api sang dashboard làm backend
Coolify → app `vision-stack-git` → env:
```
VISION_BACKEND_URL          = https://<project>.pages.dev
VISION_BACKEND_INTERNAL_KEY = <INTERNAL_KEY ở bước 2>
VISION_TOKENS_FROM_BACKEND  = true
```
Redeploy. Bỏ service `mock-backend` khỏi compose (hoặc để, không sao — chỉ không dùng).

### 5. Bootstrap token
Vào dashboard (admin) → tab **Clients** → tạo client (hoặc dùng `t_demo`) → tab **Token & Usage**
→ token client tự sinh. Đặt `VISION_CLIENT_TOKEN` / `VISION_ADMIN_TOKEN` (bước 2) bằng token
role tương ứng để dashboard tự gọi vision-api được. (Token admin: `INSERT INTO api_tokens ... role='admin', tenant_id='t_demo'` hoặc thêm 1 dòng thủ công.)

## Local dev
```bash
cp .dev.vars.example .dev.vars   # điền DATABASE_URL thật + ALLOW_INSECURE=1
npm run dev                      # wrangler pages dev -> http://localhost:8788
```

## Endpoint `/internal/*` (vision-api gọi) — khớp `../BACKEND-CONTRACT.md`
- `GET /internal/tenants`
- `GET /internal/tenants/{tid}/product-embeddings`   (dim 384, dinov2-small)
- `GET /internal/tenants/{tid}/face-embeddings`      (dim 512, arcface)
- `GET /internal/tenants/{tid}/body-embeddings`      (dim 256, OSNet reid-0277)
- `GET /internal/api-tokens`
- `POST /internal/events`
- `GET /internal/healthz`
- `POST /internal/tenants/{tid}/products` · `.../persons`  (enroll ngoài, tuỳ chọn)

## Nhận diện người (Face + Body ReID) — HƯỚNG DẪN DÙNG

`person_id` **chung** cho face và body → fusion `/v1/persons/identify` gộp 2 tín hiệu.
Body ReID chạy **chọn lọc** (1 crop đại diện / track, khi máy rảnh — KHÔNG bám stream video).

### A. Enroll người (trên dashboard, vai trò admin hoặc client)
1. Tab **Người** → **+ Thêm người**.
2. Nhập **Tên** (+ Meta JSON tuỳ chọn, vd `{"phong_ban":"KV1"}`).
3. Chọn **1–12 ảnh**. Ưu tiên ảnh **toàn thân, thấy rõ mặt**:
   - mỗi ảnh → embed **mặt to nhất** (512-d) *và* embed **toàn thân** (256-d);
   - ảnh không có mặt vẫn dùng được cho body (bảng báo `face_count` / `body_count`).
4. **Lưu & enroll** → tự lưu Neon + gọi `/admin/reload?modality=face` và `=body`. Xong là nhận diện được ngay.
5. Xoá người: nút **Xoá** ở tab Người (xoá cả face + body embedding + reload).

> Enroll từ hệ thống ngoài (không qua UI): `POST {dashboard}/internal/tenants/{tid}/persons`
> header `X-Internal-Key`, body `{name, person_id?, face_embeddings?:[[512]], body_embeddings?:[[256]]}`.
> Tự lấy embedding trước bằng `POST {vision-api}/v1/faces/embed` + `/v1/body/embed`.

### B. Test nhận diện (trên dashboard)
Tab **Test nhận diện** → **Kiểu = Người — fusion face+body** → chọn ảnh → **Nhận diện**.
Kết quả: `match` (tên + confidence) · `face_score` / `body_score` · `nguồn` (face/body) ·
màu áo/quần · `mặt thấy / KHÔNG thấy`.

### C. Client gọi trực tiếp vision-api
```bash
curl -F file=@crop_1_nguoi.jpg \
  -H "Authorization: Bearer <TOKEN client tenant>" -H "X-Tenant-ID: t_demo" \
  "https://vision-api.schoolsai.work/v1/persons/identify?top_k=5&face_threshold=0.40&body_threshold=0.5"
```
```json
{ "match": { "person_id": "p_...", "name": "...", "confidence": 0.91,
             "face_score": 0.95, "body_score": 0.86, "clothing_score": null,
             "sources": ["face","body"] },
  "attributes": { "upper_color": "red", "lower_color": "navy", ... },
  "face_visible": true, "candidates": [ ... ], "inference_ms": 71.4 }
```
Chỉ cần embedding thô: `POST /v1/body/embed` (256-d) · `POST /v1/faces/embed` (512-d).
Chỉ body / chỉ face: `POST /v1/body/search` · `POST /v1/faces/search`.

### D. Calibrate threshold (BẮT BUỘC trước khi tin số)
- `face_threshold` mặc định `0.40`, `body_threshold` `0.5` — **chưa đo trên camera thật**.
- Dựng tập gallery + probe thật (cùng người / khác người, **cùng đồ và khác đồ** riêng).
- Đo FAR/FRR theo từng ngưỡng → chọn. Body ReID cùng-đồ ~0.9+, khác-đồ tụt mạnh (dựa mặt/vóc dáng).

### E. Deploy lại dashboard (KHÔNG git-auto)
CF Pages project `vision-dashboard` **không nối git** → sửa `dashboard/` xong phải deploy tay:
```bash
cd dashboard && npm i
CLOUDFLARE_API_TOKEN=<token Pages:Edit> CLOUDFLARE_ACCOUNT_ID=<id> \
  npx wrangler pages deploy public --project-name=vision-dashboard --branch=main
```
Đổi schema Neon: `psql "$DATABASE_URL" -f schema.sql` (các lệnh đều `IF NOT EXISTS`).
