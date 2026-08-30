import { sql, json, err, reloadIndex, assertTenantAccess } from "../../_lib.js";

// GET /api/products/{id}
export async function onRequestGet({ env, params, data }) {
  const rows = await sql(env)`
    SELECT p.*, count(e.id)::int AS n_emb
    FROM products p LEFT JOIN product_embeddings e ON e.product_id=p.id
    WHERE p.id=${params.id} GROUP BY p.id`;
  if (!rows.length) return err(404, "không có sản phẩm");
  try { await assertTenantAccess(env, data, rows[0].tenant_id); } catch (e) { return err(e.status || 403, e.message); }
  return json(rows[0]);
}

// DELETE /api/products/{id}
export async function onRequestDelete({ env, params, data }) {
  const rows = await sql(env)`SELECT tenant_id FROM products WHERE id=${params.id}`;
  if (!rows.length) return json({ deleted: 0 });
  try { await assertTenantAccess(env, data, rows[0].tenant_id); } catch (e) { return err(e.status || 403, e.message); }
  await sql(env)`DELETE FROM products WHERE id=${params.id}`;
  await reloadIndex(env, rows[0].tenant_id).catch(() => {});
  return json({ deleted: 1 });
}
