# vision-stack — GPU visual recognition appliance (face + product + body ReID)

```
Proxmox promox
  └── VM 103 gpu-api (Ubuntu 22.04, GTX 1650 passthrough)
        └── Docker + NVIDIA Container Toolkit
              ├── Coolify            (quản lý/deploy/log/restart/healthcheck)
              ├── vision-api         (FastAPI + SCRFD/ArcFace + DINOv2 + OSNet ReID + FAISS in-RAM) :18090
              └── mock-backend       (FastAPI + SQLite = source-of-truth giả lập) :18091 (localhost)
```

- **vision-api** — GPU inference, 3 modality dùng chung 1 service / 1 GPU worker:
  - **face** — InsightFace SCRFD + ArcFace (nhiều mặt / ảnh), `/v1/faces/*`
  - **product** — DINOv2-S visual search, **1 ảnh = 1 sản phẩm**, so với catalog tenant, `/v1/products/*`
  - **body** (Phase 1) — Person ReID toàn thân (OMZ OSNet `person-reidentification-retail-0277`,
    embedding 256-d). Nhận lại 1 người giữa nhiều camera. `/v1/body/*` + fusion `/v1/persons/identify`
    (face + body). Quần áo mạnh nhưng **không** bền — đổi đồ thì tin cậy giảm; gait/pose = Phase 2/3.
  FAISS index/tenant/modality trong RAM, sync từ backend lúc khởi động, rebuild qua `/admin/reload?modality=`.
  Bật/tắt: `VISION_ENABLE_FACE`, `VISION_ENABLE_PRODUCTS`, `VISION_ENABLE_BODY`.
- **mock-backend** — thay chỗ backend/DB thật. Giữ tenants / persons / embeddings / events.
  Khi có backend thật: trỏ `VISION_BACKEND_URL` sang đó, implement 4 endpoint `/internal/*`
  (xem `mock-backend/app/main.py`), bỏ container này.
- DB thật **không** đặt trong VM này — đúng mô hình bạn chốt.

Đợt này: **Face + Product + Body ReID (Phase 1)**. Vehicle / OCR / gait / pose thêm sau vào cùng
`vision-api` (VRAM đo thực tế: face+product+body ~0.9GB/4GB, còn nhiều chỗ trên GTX 1650).
Body ReID chạy **chọn lọc** (1 crop đại diện / track, khi máy rảnh — KHÔNG bám stream).

> **Nhận diện người (Phase 1) đã chạy end-to-end**: enroll face+body qua dashboard
> (tab **Người**) → `POST /v1/persons/identify` trả `person_id` + confidence (fusion face+body).
> Xem `dashboard/README.md` §"Nhận diện người" và `API.md` §5.
>
> **Chưa production.** Xem [`GO-LIVE.md`](./GO-LIVE.md) (checklist blocker),
> [`BACKEND-CONTRACT.md`](./BACKEND-CONTRACT.md) (spec) và [`INTEGRATION.md`](./INTEGRATION.md)
> (hướng dẫn từng bước cho dev backend khách — schema SQL, endpoint, luồng enroll, checklist).
> Đã có: SSRF guard cho `url=`, container non-root (uid 10001), model bake sẵn trong image,
> `GET /metrics` (Prometheus).

## Boot sequence (gate `/ready`)
```
load SCRFD+ArcFace  ->  warmup GPU  ->  GET /internal/tenants
->  GET /internal/tenants/{id}/face-embeddings  ->  build FAISS  ->  ready=true
```
`/health` = liveness (luôn 200 khi process sống). `/ready` = 200 chỉ khi index sẵn sàng.

## Auth
Mọi `/v1/*` và `/admin/*`:
```
Authorization: Bearer <token>
X-Tenant-ID: <tenant>        # bắt buộc nếu token là global ("*")
X-Request-ID: <optional>     # echo lại trong response, tự sinh nếu thiếu
```
Token khai trong `VISION_API_TOKENS` (JSON). `role=admin` mới gọi được `/admin/reload`.
Rate limit theo tenant: `VISION_RATE_LIMIT_PER_MIN` (sliding-window in-process).

## Endpoints
| Method | Path | Auth | Việc |
|---|---|---|---|
| GET | `/health` | – | liveness |
| GET | `/ready` | – | readiness + trạng thái index |
| GET | `/gpu` | – | provider ORT + VRAM (nvidia-smi) |
| POST | `/v1/faces/detect` | client | ảnh → bbox + det_score |
| POST | `/v1/faces/embed` | client | ảnh → embedding 512-d (L2-norm) để enroll |
| POST | `/v1/faces/search` | client | ảnh → mỗi mặt: `person_id` + score (FAISS). `?top_k` `?threshold` |
| POST | `/v1/products/embed` · `/v1/products/search` | client | 1 ảnh = 1 sp (DINOv2-S, 384-d) |
| POST | `/v1/body/embed` | client | crop toàn thân → ReID embedding 256-d (L2-norm) để enroll |
| POST | `/v1/body/search` | client | crop toàn thân → `person_id` + score. `?top_k` `?threshold` |
| POST | `/v1/body/attributes` | client | crop → màu áo / màu quần (lọc nhanh, **KHÔNG** phải ID) |
| POST | `/v1/persons/identify` | client | crop 1 người → **fusion** face + body → `person_id` + confidence |
| POST | `/admin/reload` | admin | rebuild FAISS. `?modality=face\|product\|body\|all` `?tenant_id=` |
| GET | `/v1/index/stats` · `/v1/products/index/stats` · `/v1/body/index/stats` | client/admin | số person/vector đã index |

Body ảnh: `multipart/form-data` với `file=@anh.jpg` **hoặc** `url=<http...>`.

```bash
TOKEN=tok_demo_client
# detect
curl -F file=@a.jpg -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: t_demo" \
  http://192.168.1.50:18090/v1/faces/detect
# search
curl -F file=@a.jpg -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: t_demo" \
  'http://192.168.1.50:18090/v1/faces/search?top_k=5&threshold=0.4'
```

## Enroll 1 người (flow chuẩn)
```
# face:
POST /v1/faces/embed              -> lấy embedding[] (512-d)
POST {backend}/internal/tenants/{tid}/persons        {name, embeddings:[emb]}
POST /admin/reload?modality=face&tenant_id={tid}

# body ReID (cùng person_id với face -> fusion mới gộp được):
POST /v1/body/embed              -> embedding (256-d), crop TOÀN THÂN
POST {backend}/internal/tenants/{tid}/body-embeddings  {person_id, name, embeddings:[emb]}
POST /admin/reload?modality=body&tenant_id={tid}
```
`scripts/e2e-test.sh` chạy đúng flow này với ảnh mẫu.

## Chạy local (dev)
```bash
cp .env.example .env         # sửa INTERNAL_KEY + VISION_API_TOKENS
docker compose up -d --build
curl localhost:18090/ready
bash scripts/e2e-test.sh
```

## Deploy bằng Coolify
1. `bash scripts/build.sh` trên VM (tạo `vision-api:latest`, `mock-backend:latest`).
2. Nếu stack local đang chạy: `docker compose down` (nhả cổng 18090).
3. Coolify → Project → **+ New → Docker Compose Empty** → server `localhost`.
4. Dán `docker-compose.coolify.yml`.
5. Tab **Environment Variables**: set `INTERNAL_KEY`, `VISION_API_TOKENS` (và các giá trị trong `.env`).
6. **Deploy**. GPU chạy nhờ block `deploy.resources.reservations.devices` + NVIDIA runtime đã cấu hình trên host.
7. Model buffalo_l (~180MB) tự tải về **volume** `models` ở lần chạy đầu (cần internet 1 lần), lần sau không tải lại.

## Push Git sau
```bash
cd /opt/vision-stack
git init && git add -A && git commit -m "vision-stack: face v1"
git remote add origin <repo-url>
git push -u origin main
```
Rồi đổi Coolify resource sang kiểu deploy-from-Git (build tự động khi push).

## Nâng cấp GPU (GTX 1650 → RTX 3060/3090)
Không đổi kiến trúc. Chỉ:
- đổi `INSIGHTFACE_MODEL` sang pack to hơn nếu muốn (vd `antelopev2`),
- ~~OSNet body ReID~~ ✅ đã có (Phase 1). bật thêm vehicle detector / OCR biển số / gait / pose — VRAM lớn hơn cho phép resident hết,
- tăng `VISION_GPU_CONCURRENCY`.

## Cấu trúc
```
vision-stack/
├── docker-compose.yml            # build local + GPU
├── docker-compose.coolify.yml    # image-only cho Coolify
├── .env.example
├── scripts/{build.sh,e2e-test.sh}
├── vision-api/   app/{main,config,deps,engine,index,backend}.py + Dockerfile
└── mock-backend/ app/main.py + Dockerfile
```
