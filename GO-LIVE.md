# GO-LIVE checklist — vision-api

Trạng thái: **POC chạy được, CHƯA production.** Bảng dưới là việc còn lại.
Owner: **me** = làm được trong code/hạ tầng hiện có · **you** = cần quyết định / dữ liệu / hệ thống bên ngoài.

## 🔴 Blocker — không lên production nếu chưa xong

| # | Việc | Owner | Trạng thái | Verify |
|---|---|---|---|---|
| 1 | **SSRF guard cho `url=`** — chặn IP nội bộ/loopback/link-local, không theo redirect, allowlist domain + kill-switch | me | ✅ DONE (`app/net.py`, `VISION_ALLOW_URL_FETCH`, `VISION_URL_ALLOWLIST`) | `curl -F url=http://169.254.169.254/ ...` → 400 ; `-F url=http://localhost:8000/ ...` → 400 |
| 2 | **Đấu backend thật** thay `mock-backend` | you | ⛔ chờ | implement 7 endpoint trong `BACKEND-CONTRACT.md`, set `VISION_BACKEND_URL` |
| 3 | **Rotate & quản lý secrets** — token client/admin, `INTERNAL_KEY` hiện là bản dev do tool sinh | you | ⛔ chờ | sinh token mới (≥32 byte random), set qua Coolify secrets, xoá bản cũ khỏi `.env`/notes/memory |
| 4 | **Kênh truyền có mã hoá** — hiện LAN plaintext, Bearer token bay rõ | you | ⛔ chờ | Tailscale/WireGuard giữa client ↔ VM, hoặc TLS termination (Coolify proxy + domain nội bộ) |
| 5 | **Calibrate threshold** — `MATCH_THRESHOLD=0.40` chưa đo trên dữ liệu thật | you (data) + me (script) | ⛔ chờ data | dựng tập gallery/probe thật → đo FAR/FRR → chọn ngưỡng theo yêu cầu nghiệp vụ, ghi lại |
| 6 | **Pháp lý sinh trắc học** — consent, retention, xoá theo yêu cầu, audit | you | ⛔ chờ | có quy trình consent; `DELETE /internal/tenants/{tid}/persons/{pid}` đã sẵn; audit qua `/internal/events` |

## 🟠 Nên có trước hoặc ngay sau go-live

| Việc | Owner | Trạng thái |
|---|---|---|
| **Container non-root** (uid 10001 + gosu drop-priv) | me | ✅ DONE — verify: `docker exec <vision-api> id` → `uid=10001(app)` |
| **Vendor model vào image** (không tải GitHub lúc cold-start) | me | ✅ DONE — model bake ở `/opt/models`, entrypoint seed volume |
| **/metrics Prometheus** (latency, QPS/tenant, VRAM, FAISS size, ratelimit) | me | ✅ DONE — `GET /metrics` ; nhớ **firewall** cổng 18090 chỉ cho Prometheus + client |
| **PIL decompression-bomb guard** + cap upload theo stream | me | ✅ DONE (`VISION_MAX_IMAGE_PIXELS`, đọc `MAX_BYTES+1`) |
| CI/CD: push git → build image → deploy (thay build tay trên VM) | you/me | ⛔ — repo đã init, cần remote + workflow |
| Backup: snapshot volume `models` + dump nguồn embeddings từ backend | you | ⛔ |
| Alert khi `/ready`=false hoặc `vision_ready`=0 | you | ⛔ — có metric, cần Alertmanager/Grafana |
| Log tập trung (Coolify log drain → Loki/ELK) | you | ⛔ |
| Giới hạn vector/tenant + eviction (chống RAM phình) | me | ⛔ — chưa có; ổn khi tenant nhỏ |

## 🟡 Khi scale / đổi GPU

- Queue (Redis) + batch inference khi nhiều camera bắn burst — GTX 1650 + `GPU_CONCURRENCY=1` là nút thắt
- HA: hiện 1 GPU / 1 VM / 1 container — mọi thứ chết nếu VM chết
- Phase 2: OSNet (body ReID) + vehicle + OCR biển số vào cùng `vision-api` (VRAM còn ~3.4GB)
- GTX 1650 → RTX 3060/3090: không đổi kiến trúc, chỉ đổi `INSIGHTFACE_MODEL` + `GPU_CONCURRENCY`
- DNS-rebinding TOCTOU: SSRF guard resolve rồi mới fetch — vẫn còn khe hẹp. Khoá chặt bằng `VISION_URL_ALLOWLIST` hoặc `VISION_ALLOW_URL_FETCH=false` (chỉ nhận upload)

## Cấu hình production tối thiểu (env)

```
VISION_ALLOW_URL_FETCH=false          # hoặc set VISION_URL_ALLOWLIST=domain-cua-ban.com
VISION_API_TOKENS={"<token 32B random>":{"tenant":"t_acme","role":"client"}, ...}
INTERNAL_KEY=<32B random>
VISION_BACKEND_URL=https://backend-that/api
VISION_RATE_LIMIT_PER_MIN=<theo tải thật>
VISION_METRICS_ENABLED=true           # + firewall cổng
MATCH_THRESHOLD=<sau khi calibrate>
```
