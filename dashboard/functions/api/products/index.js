import { sql, json, err, genId, toVec, embedImage, reloadIndex, assertTenantAccess } from "../../_lib.js";

// GET /api/products?tenant=&q=&page=1&per=50
export async function onRequestGet({ env, request, data }) {
  const u = new URL(request.url);
  const tenant = u.searchParams.get("tenant") || env.DEFAULT_TENANT;
  try { await assertTenantAccess(env, data, tenant); } catch (e) { return err(e.status || 403, e.message); }

  const q = (u.searchParams.get("q") || "").trim();
  const per = Math.min(200, Math.max(1, +(u.searchParams.get("per") || 50)));
  const page = Math.max(1, +(u.searchParams.get("page") || 1));
  const off = (page - 1) * per;
  const like = `%${q}%`;

  const rows = q
    ? await sql(env)`
        SELECT p.id, p.sku, p.name, p.meta, p.created_at,
               count(e.id)::int AS n_emb
        FROM products p LEFT JOIN product_embeddings e ON e.product_id=p.id
        WHERE p.tenant_id=${tenant} AND (p.name ILIKE ${like} OR p.sku ILIKE ${like})
        GROUP BY p.id ORDER BY p.created_at DESC LIMIT ${per} OFFSET ${off}`
    : await sql(env)`
        SELECT p.id, p.sku, p.name, p.meta, p.created_at,
               count(e.id)::int AS n_emb
        FROM products p LEFT JOIN product_embeddings e ON e.product_id=p.id
        WHERE p.tenant_id=${tenant}
        GROUP BY p.id ORDER BY p.created_at DESC LIMIT ${per} OFFSET ${off}`;

  const [{ total }] = await sql(env)`SELECT count(*)::int AS total FROM products WHERE tenant_id=${tenant}`;
  return json({ tenant, page, per, total, products: rows });
}

// POST /api/products  (multipart)  fields: tenant, sku, name, meta(json), images (1+ file)
export async function onRequestPost({ env, request, data }) {
  let form;
  try { form = await request.formData(); } catch { return err(400, "cần multipart/form-data"); }
  const tenant = form.get("tenant") || env.DEFAULT_TENANT;
  try { await assertTenantAccess(env, data, tenant); } catch (e) { return err(e.status || 403, e.message); }

  const name = (form.get("name") || "").toString().trim();
  const skuv = (form.get("sku") || "").toString().trim() || null;
  if (!name) return err(422, "cần name");
  let meta = {};
  try { meta = JSON.parse(form.get("meta") || "{}"); } catch {}

  const files = form.getAll("images").filter((f) => f && typeof f.arrayBuffer === "function" && f.size > 0);
  if (!files.length) return err(422, "cần ít nhất 1 ảnh");
  if (files.length > 12) return err(422, "tối đa 12 ảnh / SKU");

  // embed từng ảnh qua vision-api
  const vecs = [];
  for (const f of files) {
    const v = await embedImage(env, tenant, f).catch((e) => {
      throw Object.assign(new Error("embed ảnh lỗi: " + e.message), { status: 502 });
    });
    vecs.push(v);
  }

  await sql(env)`INSERT INTO tenants (id,name) VALUES (${tenant},${tenant}) ON CONFLICT (id) DO NOTHING`;
  const pid = genId("p");
  await sql(env)`
    INSERT INTO products (id, tenant_id, sku, name, meta)
    VALUES (${pid}, ${tenant}, ${skuv}, ${name}, ${JSON.stringify(meta)}::jsonb)`;
  for (const v of vecs)
    await sql(env)`
      INSERT INTO product_embeddings (id, product_id, tenant_id, vec)
      VALUES (${genId("pe")}, ${pid}, ${tenant}, ${toVec(v)}::vector)`;

  await reloadIndex(env, tenant).catch(() => {});
  return json({ product_id: pid, name, sku: skuv, embedding_count: vecs.length }, 201);
}
