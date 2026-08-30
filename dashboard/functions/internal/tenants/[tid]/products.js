import { sql, json, err, genId, toVec } from "../../../_lib.js";

// POST /internal/tenants/{tid}/products  (tùy chọn — cho hệ thống enroll của khách)
// body: { name, sku?, product_id?, embeddings: [[384 số], ...] }
export async function onRequestPost({ env, params, request }) {
  const tid = params.tid;
  let b;
  try { b = await request.json(); } catch { return err(400, "body JSON không hợp lệ"); }
  if (!b?.name || !Array.isArray(b.embeddings) || !b.embeddings.length)
    return err(422, "cần name + embeddings[]");
  for (const v of b.embeddings)
    if (!Array.isArray(v) || v.length !== 384) return err(422, "mỗi embedding phải 384 chiều");

  await sql(env)`INSERT INTO tenants (id,name) VALUES (${tid},${tid}) ON CONFLICT (id) DO NOTHING`;
  const pid = b.product_id || genId("p");
  await sql(env)`
    INSERT INTO products (id, tenant_id, sku, name) VALUES (${pid}, ${tid}, ${b.sku || null}, ${b.name})
    ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, sku=EXCLUDED.sku, updated_at=now()`;
  for (const v of b.embeddings)
    await sql(env)`
      INSERT INTO product_embeddings (id, product_id, tenant_id, vec)
      VALUES (${genId("pe")}, ${pid}, ${tid}, ${toVec(v)}::vector)`;

  const [{ count }] = await sql(env)`SELECT count(*)::int FROM product_embeddings WHERE product_id=${pid}`;
  return json({ product_id: pid, name: b.name, sku: b.sku || null, embedding_count: count }, 201);
}
