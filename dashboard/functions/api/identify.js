import { sql, json, err, vision, assertTenantAccess } from "../_lib.js";

// POST /api/identify  (multipart) fields: tenant, image (file), top_k?, face_threshold?, body_threshold?
// -> vision-api /v1/persons/identify + enrich tên/meta người từ DB
export async function onRequestPost({ env, request, data }) {
  let form;
  try { form = await request.formData(); } catch { return err(400, "cần multipart/form-data"); }
  const tenant = form.get("tenant") || env.DEFAULT_TENANT;
  try { await assertTenantAccess(env, data, tenant); } catch (e) { return err(e.status || 403, e.message); }

  const file = form.get("image");
  if (!file || typeof file.arrayBuffer !== "function") return err(422, "cần field 'image'");

  const fd = new FormData();
  fd.append("file", file, "q.jpg");
  const qs = new URLSearchParams();
  for (const k of ["top_k", "face_threshold", "body_threshold"]) {
    const v = form.get(k);
    if (v) qs.set(k, String(v));
  }

  let res;
  try {
    res = await vision(env, `/v1/persons/identify${qs.toString() ? "?" + qs : ""}`, { method: "POST", tenant, body: fd });
  } catch (e) {
    return err(502, String(e.message || e));
  }

  const ids = [res.match?.person_id, ...(res.candidates || []).map((c) => c.person_id)].filter(Boolean);
  let meta = {};
  if (ids.length) {
    const rows = await sql(env)`SELECT id, name, meta FROM persons WHERE id = ANY(${[...new Set(ids)]})`;
    meta = Object.fromEntries(rows.map((r) => [r.id, r]));
  }
  const enr = (c) => (c ? { ...c, ...(meta[c.person_id] || {}) } : null);

  return json({
    tenant_id: tenant,
    attributes: res.attributes || null,
    face_visible: res.face_visible,
    inference_ms: res.inference_ms,
    match: enr(res.match),
    candidates: (res.candidates || []).map(enr),
  });
}
