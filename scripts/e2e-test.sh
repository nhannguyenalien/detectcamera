#!/usr/bin/env bash
# Test end-to-end: enroll 1 khuôn mặt -> đẩy vào backend -> reload -> search lại.
set -euo pipefail

API=${API:-http://localhost:18090}
BE=${BE:-http://localhost:18091}
CLIENT_TOKEN=${CLIENT_TOKEN:-tok_demo_client}
ADMIN_TOKEN=${ADMIN_TOKEN:-tok_admin_root}
INTERNAL_KEY=${INTERNAL_KEY:-dev-internal-key}
TENANT=${TENANT:-t_demo}
IMG=${IMG:-/tmp/face1.jpg}

echo "== /ready =="
curl -fsS "$API/ready"; echo

echo "== lấy ảnh test =="
[ -f "$IMG" ] || curl -fsSL -o "$IMG" https://ultralytics.com/images/zidane.jpg
ls -la "$IMG"

echo "== embed (enroll) =="
EMB_JSON=$(curl -fsS -X POST "$API/v1/faces/embed" \
  -H "Authorization: Bearer $CLIENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -F "file=@$IMG")
echo "$EMB_JSON" | head -c 300; echo " ..."

VEC=$(python3 -c "import sys,json;d=json.load(sys.stdin);print(json.dumps(d['faces'][0]['embedding']))" <<<"$EMB_JSON")

echo "== push person vào backend =="
curl -fsS -X POST "$BE/internal/tenants/$TENANT/persons" \
  -H "X-Internal-Key: $INTERNAL_KEY" -H "Content-Type: application/json" \
  -d "{\"name\":\"Zidane\",\"embeddings\":[$VEC]}"; echo

echo "== admin reload =="
curl -fsS -X POST "$API/admin/reload?tenant_id=$TENANT" \
  -H "Authorization: Bearer $ADMIN_TOKEN"; echo

echo "== search (phải match Zidane) =="
curl -fsS -X POST "$API/v1/faces/search?top_k=3" \
  -H "Authorization: Bearer $CLIENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -F "file=@$IMG" | python3 -m json.tool

echo "== events đã ghi ở backend =="
curl -fsS "$BE/internal/events?tenant_id=$TENANT&limit=3" -H "X-Internal-Key: $INTERNAL_KEY" | python3 -m json.tool
