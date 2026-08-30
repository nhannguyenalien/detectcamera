import { sql, json, err, fromVec } from "../../../_lib.js";

// GET /internal/tenants/{tid}/product-embeddings  -> vision-api dựng FAISS
export async function onRequestGet({ env, params }) {
  const tid = params.tid;
  const t = await sql(env)`SELECT 1 FROM tenants WHERE id=${tid}`;
  if (!t.length) return err(404, "tenant không tồn tại");

  const rows = await sql(env)`
    SELECT p.id AS product_id, p.sku, p.name,
           coalesce(
             json_agg(e.vec::text ORDER BY e.created_at) FILTER (WHERE e.id IS NOT NULL),
             '[]'
           ) AS vecs
    FROM products p
    LEFT JOIN product_embeddings e ON e.product_id = p.id
    WHERE p.tenant_id = ${tid}
    GROUP BY p.id
    ORDER BY p.created_at`;

  const products = rows.map((r) => ({
    product_id: r.product_id,
    sku: r.sku,
    name: r.name,
    embeddings: (r.vecs || []).map((v) => fromVec(v)),
  }));

  return json({ tenant_id: tid, dim: 384, model: "dinov2-small", products });
}
