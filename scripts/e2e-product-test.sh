#!/usr/bin/env bash
# Test product visual search: enroll 1 ảnh sp -> search lại đúng, ảnh khác thì không match.
# Chạy trên VM (mock-backend không expose port -> phần backend call qua docker exec).
set -euo pipefail

API=${API:-http://localhost:18090}
CLIENT_TOKEN=${CLIENT_TOKEN:-tok_demo_client}
ADMIN_TOKEN=${ADMIN_TOKEN:-tok_admin_root}
INTERNAL_KEY=${INTERNAL_KEY:-dev-internal-key}
TENANT=${TENANT:-t_demo}
BE_CONTAINER=${BE_CONTAINER:?set BE_CONTAINER=<mock-backend container name>}

IMG_A=/tmp/prod_a.jpg     # "sản phẩm A"
IMG_B=/tmp/prod_b.jpg     # ảnh khác -> không được match A
[ -f "$IMG_A" ] || curl -fsSL -o "$IMG_A" https://ultralytics.com/images/bus.jpg
[ -f "$IMG_B" ] || curl -fsSL -o "$IMG_B" https://ultralytics.com/images/zidane.jpg

echo "== /ready =="; curl -fsS "$API/ready" | python3 -m json.tool | grep -E '"ready"|product|face' | head

echo "== embed sản phẩm A =="
EMB=$(curl -fsS -X POST "$API/v1/products/embed" \
  -H "Authorization: Bearer $CLIENT_TOKEN" -H "X-Tenant-ID: $TENANT" -F "file=@$IMG_A")
DIM=$(python3 -c "import sys,json;print(len(json.loads(sys.argv[1])['embedding']))" "$EMB")
VEC=$(python3 -c "import sys,json;print(json.dumps(json.loads(sys.argv[1])['embedding']))" "$EMB")
echo "embedding dim = $DIM"

echo "== push product vào backend =="
docker exec "$BE_CONTAINER" python -c "
import urllib.request, json
d=json.dumps({'name':'Sản phẩm A','sku':'SKU-A-001','embeddings':[$VEC]}).encode()
r=urllib.request.Request('http://localhost:9000/internal/tenants/$TENANT/products',
    data=d, headers={'Content-Type':'application/json','X-Internal-Key':'$INTERNAL_KEY'}, method='POST')
print(urllib.request.urlopen(r).read().decode())
"

echo "== admin reload (product) =="
curl -fsS -X POST "$API/admin/reload?modality=product&tenant_id=$TENANT" \
  -H "Authorization: Bearer $ADMIN_TOKEN"; echo

echo "== search bằng ảnh A (PHẢI match 'Sản phẩm A', score cao) =="
curl -fsS -X POST "$API/v1/products/search" \
  -H "Authorization: Bearer $CLIENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -F "file=@$IMG_A" | python3 -m json.tool

echo "== search bằng ảnh B (KHÔNG được match, hoặc score thấp) =="
curl -fsS -X POST "$API/v1/products/search" \
  -H "Authorization: Bearer $CLIENT_TOKEN" -H "X-Tenant-ID: $TENANT" \
  -F "file=@$IMG_B" | python3 -m json.tool

echo "== dọn =="
docker exec "$BE_CONTAINER" python -c "
import urllib.request, json
req=urllib.request.Request('http://localhost:9000/internal/tenants/$TENANT/product-embeddings',
    headers={'X-Internal-Key':'$INTERNAL_KEY'})
for p in json.loads(urllib.request.urlopen(req).read())['products']:
    d=urllib.request.Request('http://localhost:9000/internal/tenants/$TENANT/products/'+p['product_id'],
        headers={'X-Internal-Key':'$INTERNAL_KEY'}, method='DELETE')
    urllib.request.urlopen(d)
print('cleaned test products')
"
curl -fsS -X POST "$API/admin/reload?modality=product&tenant_id=$TENANT" -H "Authorization: Bearer $ADMIN_TOKEN"; echo
