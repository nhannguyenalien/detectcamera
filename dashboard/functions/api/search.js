import { sql, json, err, vision, assertTenantAccess } from "../_lib.js";

// POST /api/search  (multipart)  fields: tenant, image (file), threshold?, top_k?
// -> gọi vision-api /v1/products/search rồi enrich metadata từ DB
export async function onRequestPost({ env, request, data }) {
  let form;
  try { form = await request.formData(); } catch { return err(400, "cần multipart/form-data"); }
  const tenant = form.get("tenant") || env.DEFAULT_TENANT;
  try { await assertTenantAccess(env, data, tenant); } catch (e) { return err(e.status || 403, e.message); }

  const file = form.get("image");
  if (!file || typeof file.arrayBuffer !== "function") return err(422, "cần field 'image'");
  const threshold = form.get("threshold");
  const top_k = form.get("top_k") || 10;

  const fd = new FormData();
  fd.append("file", file, "q.jpg");
  const qs = new URLSearchParams({ top_k: String(top_k) });
  if (threshold) qs.set("threshold", String(threshold));

  let res;
  try {
    res = await vision(env, `/v1/products/search?${qs}`, { method: "POST", tenant, body: fd });
  } catch (e) {
    return err(502, String(e.message || e));
  }

  const ids = [res.match?.product_id, ...(res.candidates || []).map((c) => c.product_id)].filter(Boolean);
  let metaById = {};
  if (ids.length) {
    const rows = await sql(env)`SELECT id, sku, name, meta FROM products WHERE id = ANY(${[...new Set(ids)]})`;
    metaById = Object.fromEntries(rows.map((r) => [r.id, r]));
  }
  const enrich = (c) => (c ? { ...c, ...(metaById[c.product_id] || {}) } : null);

  return json({
    tenant_id: tenant,
    threshold: res.threshold,
    inference_ms: res.inference_ms,
    match: enrich(res.match),
    candidates: (res.candidates || []).map(enrich),
    index: res.index,
  });
}
