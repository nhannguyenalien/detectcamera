import { sql, json, err, genId, toVec, vision, reloadIndex, assertTenantAccess } from "../../_lib.js";

// POST /api/products/bulk  { tenant, items:[{ name, sku?, meta?, image_urls:[...] }] }
// Mỗi item: embed từng URL qua vision-api -> lưu -> reload 1 lần ở cuối.
export async function onRequestPost({ env, request, data }) {
  let b;
  try { b = await request.json(); } catch { return err(400, "body JSON không hợp lệ"); }
  const tenant = b.tenant || env.DEFAULT_TENANT;
  try { await assertTenantAccess(env, data, tenant); } catch (e) { return err(e.status || 403, e.message); }
  const items = Array.isArray(b.items) ? b.items : [];
  if (!items.length) return err(422, "items[] rỗng");
  if (items.length > 200) return err(422, "tối đa 200 item / lần");

  await sql(env)`INSERT INTO tenants (id,name) VALUES (${tenant},${tenant}) ON CONFLICT (id) DO NOTHING`;

  const results = [];
  for (const it of items) {
    const name = String(it.name || "").trim();
    if (!name || !Array.isArray(it.image_urls) || !it.image_urls.length) {
      results.push({ name: name || "?", ok: false, error: "thiếu name hoặc image_urls" });
      continue;
    }
    try {
      const vecs = [];
      for (const u of it.image_urls.slice(0, 12)) {
        const fd = new FormData();
        fd.append("url", String(u));
        const d = await vision(env, "/v1/products/embed", { method: "POST", tenant, body: fd });
        if (Array.isArray(d.embedding)) vecs.push(d.embedding);
      }
      if (!vecs.length) throw new Error("không embed được ảnh nào");
      const pid = genId("p");
      await sql(env)`
        INSERT INTO products (id, tenant_id, sku, name, meta)
        VALUES (${pid}, ${tenant}, ${it.sku || null}, ${name}, ${JSON.stringify(it.meta || {})}::jsonb)`;
      for (const v of vecs)
        await sql(env)`INSERT INTO product_embeddings (id, product_id, tenant_id, vec)
                       VALUES (${genId("pe")}, ${pid}, ${tenant}, ${toVec(v)}::vector)`;
      results.push({ name, product_id: pid, ok: true, embeddings: vecs.length });
    } catch (e) {
      results.push({ name, ok: false, error: String(e.message || e).slice(0, 200) });
    }
  }
  await reloadIndex(env, tenant).catch(() => {});
  const ok = results.filter((r) => r.ok).length;
  return json({ tenant, added: ok, failed: results.length - ok, results }, 201);
}
