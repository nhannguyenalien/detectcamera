# Storage layout — VM 103 `gpu-api` (192.168.1.50)

Đọc trước khi ghi file lớn, tạo Docker volume, hoặc chọn chỗ để dataset/model.
Dành cho dev **và AI agent**.

## Có 2 vùng lưu trữ, tốc độ KHÁC NHAU

| Mount | Thiết bị | Kích thước | Tốc độ | Còn trống |
|---|---|---|---|---|
| `/` (root) | `local-lvm` (SSD, thin LVM) | 146 GB | **Nhanh** (SSD, random-IO tốt) | ~100 GB |
| `/data` | `hdd3t` (`/dev/sdb`, raw trên HDD) | 492 GB | **Chậm** (ổ quay, random-IO kém, sequential ~OK) | ~467 GB |

`/data`: ext4, owner `gpu:gpu`, tự mount khi reboot (`/etc/fstab`, `nofail`). Ghi thẳng, không cần sudo.

## Quy tắc — cái gì để ĐÂU

### ✅ ĐƯỢC để trên `/data` (HDD, chậm nhưng to)
- **Model weights lớn** ít đổi: `*.onnx`, `*.pt`, `*.safetensors`, InsightFace packs, YOLO weights, LLM GGUF…
- **Datasets** / ảnh training / video mẫu
- **Backup**: dump DB, snapshot embeddings từ backend, export config
- **Artifacts / output**: ảnh đã annotate, kết quả batch, report
- **Log nguội** cần giữ lâu (rotate sang đây), media
- Docker **named volume cho dữ liệu ít IO** (vd file tĩnh, model cache) — trỏ bằng bind `/data/<tên>`

### ❌ KHÔNG để trên `/data` (sẽ chậm rõ rệt / hỏng hiệu năng)
- `/var/lib/docker` (overlay2 build layers) — build image sẽ chậm gấp nhiều lần
- **Database hot data**: Postgres, SQLite đang ghi nhiều, Redis AOF, FAISS index file nếu swap liên tục
- Dữ liệu của **Coolify** (`/data/coolify/...` của Coolify là chuyện khác — đó là path Coolify tự tạo trên `/`, ĐỪNG nhầm với `/data` này)
- Bất cứ thứ gì latency-sensitive: cache nóng, WAL, socket, lock file
- Code repo đang build, `node_modules`, venv đang cài

→ Những cái ❌ giữ trên `/` (SSD). Nếu `/` gần đầy thì dọn Docker (`docker system prune`), đừng move sang HDD.

## Cách dùng nhanh

```bash
# ghi thẳng
cp bigmodel.onnx /data/models/
mkdir -p /data/datasets /data/backups /data/artifacts

# Docker: bind vào /data cho dữ liệu tĩnh/lớn
#   docker run -v /data/models:/models ...
# hoặc trong compose:
#   volumes:
#     - /data/models:/models:ro
```

## Kiểm tra dung lượng

```bash
df -h / /data                 # còn trống bao nhiêu
du -sh /data/* 2>/dev/null    # gì đang chiếm chỗ trên HDD
```

## Ghi chú vận hành

- `/data` là **1 file raw** trên storage `hdd3t` của Proxmox (`/mnt/hdd3tb/images/103/vm-103-disk-0.raw`), sparse — chỉ tốn dung lượng thật khi ghi. `discard`/`fstrim` đã bật để trả lại chỗ khi xoá.
- `hdd3t` trên host còn ~1.9 TB → mở rộng `/data` được: (host) `qm resize 103 scsi1 <size>G` rồi (guest) `resize2fs /dev/sdb`.
- Đừng đặt thứ mà **mất là toi** chỉ trên `/data` mà không backup nơi khác — nó vẫn là 1 ổ đơn, không RAID.
