# vision-stack — GPU face recognition appliance

```
Proxmox promox
  └── VM 103 gpu-api (Ubuntu 22.04, GTX 1650 passthrough)
        └── Docker + NVIDIA Container Toolkit
              ├── Coolify            (quản lý/deploy/log/restart/healthcheck)
              ├── vision-api         (FastAPI + SCRFD + ArcFace + FAISS in-RAM)   :18090
              └── mock-backend       (FastAPI + SQLite = source-of-truth giả lập) :18091 (localhost)
```

- **vision-api** — GPU inference. Face detect / embed / search. FAISS index/tenant trong RAM,
  sync từ backend lúc khởi động, rebuild qua `/admin/reload`.
- **mock-backend** — thay chỗ backend/DB thật. Giữ tenants / persons / embeddings / events.
  Khi có backend thật: trỏ `VISION_BACKEND_URL` sang đó, implement 4 endpoint `/internal/*`
  (xem `mock-backend/app/main.py`), bỏ container này.
- DB thật **không** đặt trong VM này — đúng mô hình bạn chốt.

Đợt này: **chỉ Face**, **LAN only** (chưa Tailscale/domain). Body ReID / Vehicle / OCR thêm sau
vào cùng `vision-api` (đo VRAM rồi mới bật, GTX 1650 chỉ 4GB).

> **Chưa production.** Xem [`GO-LIVE.md`](./GO-LIVE.md) (checklist blocker) và
> [`BACKEND-CONTRACT.md`](./BACKEND-CONTRACT.md) (spec backend thật cần implement).
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
| POST | `/admin/reload` | admin | rebuild FAISS. `?tenant_id=` hoặc bỏ trống = tất cả |
| GET | `/v1/index/stats` | client/admin | số person/vector đã index |

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
POST /v1/faces/embed              -> lấy embedding[]
POST {backend}/internal/tenants/{tid}/persons  {name, embeddings:[emb]}
POST /admin/reload?tenant_id={tid}
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
- bật thêm module trong `vision-api` (OSNet body ReID, vehicle detector, OCR biển số) — VRAM lớn hơn cho phép resident hết,
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
