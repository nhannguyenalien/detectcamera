# vision-dashboard

Dashboard quản lý vision-api + **backend `/internal/*` thật** (thay `mock-backend`).
Cloudflare Pages + Pages Functions + Neon (Postgres/pgvector). Ảnh chỉ dùng để embed rồi bỏ.

```
Cloudflare Pages
 ├─ public/              Tabler UI (admin + portal client)
 ├─ functions/api/*      dashboard API   ── sau Cloudflare Access
 └─ functions/internal/* backend cho vision-api gọi ── sau X-Internal-Key (exclude khỏi Access)
        │
        ▼  Neon (tenants, api_tokens, products, product_embeddings, events)
        ▲
        └─ khi enroll: gọi vision-api /v1/products/embed → lưu vec → /admin/reload
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
| Test nhận diện | ✓ | ✓ (tenant mình) |
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
- `GET /internal/api-tokens`
- `POST /internal/events`
- `GET /internal/healthz`
- `POST /internal/tenants/{tid}/products`, `DELETE .../products/{pid}`  (tuỳ chọn)
