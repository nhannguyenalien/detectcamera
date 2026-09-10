import { sql, json, err, genId, toVec, embedFace, embedBody, reloadIndex, assertTenantAccess } from "../../_lib.js";

// GET /api/persons/{id}
export async function onRequestGet({ env, params, data }) {
  const rows = await sql(env)`
    SELECT p.*, count(distinct f.id)::int AS n_face, count(distinct b.id)::int AS n_body
    FROM persons p
    LEFT JOIN face_embeddings f ON f.person_id=p.id
    LEFT JOIN body_embeddings b ON b.person_id=p.id
    WHERE p.id=${params.id} GROUP BY p.id`;
  if (!rows.length) return err(404, "không có người này");
  try { await assertTenantAccess(env, data, rows[0].tenant_id); } catch (e) { return err(e.status || 403, e.message); }
  return json(rows[0]);
}

// DELETE /api/persons/{id}
export async function onRequestDelete({ env, params, data }) {
  const rows = await sql(env)`SELECT tenant_id FROM persons WHERE id=${params.id}`;
  if (!rows.length) return json({ deleted: 0 });
  try { await assertTenantAccess(env, data, rows[0].tenant_id); } catch (e) { return err(e.status || 403, e.message); }
  await sql(env)`DELETE FROM persons WHERE id=${params.id}`;
  await Promise.all([
    reloadIndex(env, rows[0].tenant_id, "face").catch(() => {}),
    reloadIndex(env, rows[0].tenant_id, "body").catch(() => {}),
  ]);
  return json({ deleted: 1 });
}

// POST /api/persons/{id}  (multipart images) — thêm ảnh cho người có sẵn
export async function onRequestPost({ env, params, request, data }) {
  const rows = await sql(env)`SELECT tenant_id FROM persons WHERE id=${params.id}`;
  if (!rows.length) return err(404, "không có người này");
  const tenant = rows[0].tenant_id;
  try { await assertTenantAccess(env, data, tenant); } catch (e) { return err(e.status || 403, e.message); }

  let form;
  try { form = await request.formData(); } catch { return err(400, "cần multipart/form-data"); }
  const files = form.getAll("images").filter((f) => f && typeof f.arrayBuffer === "function" && f.size > 0);
  if (!files.length) return err(422, "cần ít nhất 1 ảnh");
  if (files.length > 12) return err(422, "tối đa 12 ảnh");

  let nf = 0, nb = 0;
  for (const f of files) {
    const [fv, bv] = await Promise.all([
      embedFace(env, tenant, f).catch(() => null),
      embedBody(env, tenant, f).catch(() => null),
    ]);
    if (fv) { await sql(env)`INSERT INTO face_embeddings (id,person_id,tenant_id,vec) VALUES (${genId("fe")},${params.id},${tenant},${toVec(fv)}::vector)`; nf++; }
    if (bv) { await sql(env)`INSERT INTO body_embeddings (id,person_id,tenant_id,vec) VALUES (${genId("be")},${params.id},${tenant},${toVec(bv)}::vector)`; nb++; }
  }
  await Promise.all([
    reloadIndex(env, tenant, "face").catch(() => {}),
    reloadIndex(env, tenant, "body").catch(() => {}),
  ]);
  return json({ person_id: params.id, added_face: nf, added_body: nb });
}
