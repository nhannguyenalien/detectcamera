import { sql, json, err, genId, toVec, embedFace, embedBody, reloadIndex, assertTenantAccess } from "../../_lib.js";

// GET /api/persons?tenant=&q=&page=1&per=50
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
        SELECT p.id, p.name, p.meta, p.created_at,
               count(distinct f.id)::int AS n_face, count(distinct b.id)::int AS n_body
        FROM persons p
        LEFT JOIN face_embeddings f ON f.person_id=p.id
        LEFT JOIN body_embeddings b ON b.person_id=p.id
        WHERE p.tenant_id=${tenant} AND p.name ILIKE ${like}
        GROUP BY p.id ORDER BY p.created_at DESC LIMIT ${per} OFFSET ${off}`
    : await sql(env)`
        SELECT p.id, p.name, p.meta, p.created_at,
               count(distinct f.id)::int AS n_face, count(distinct b.id)::int AS n_body
        FROM persons p
        LEFT JOIN face_embeddings f ON f.person_id=p.id
        LEFT JOIN body_embeddings b ON b.person_id=p.id
        WHERE p.tenant_id=${tenant}
        GROUP BY p.id ORDER BY p.created_at DESC LIMIT ${per} OFFSET ${off}`;

  const [{ total }] = await sql(env)`SELECT count(*)::int AS total FROM persons WHERE tenant_id=${tenant}`;
  return json({ tenant, page, per, total, persons: rows });
}

// POST /api/persons  (multipart)  fields: tenant, name, meta(json), images (1..12 file)
// Mỗi ảnh: embed face (mặt to nhất) + embed body (cả crop). Lưu + reload face & body.
export async function onRequestPost({ env, request, data }) {
  let form;
  try { form = await request.formData(); } catch { return err(400, "cần multipart/form-data"); }
  const tenant = form.get("tenant") || env.DEFAULT_TENANT;
  try { await assertTenantAccess(env, data, tenant); } catch (e) { return err(e.status || 403, e.message); }

  const name = (form.get("name") || "").toString().trim();
  if (!name) return err(422, "cần name");
  let meta = {};
  try { meta = JSON.parse(form.get("meta") || "{}"); } catch {}

  const files = form.getAll("images").filter((f) => f && typeof f.arrayBuffer === "function" && f.size > 0);
  if (!files.length) return err(422, "cần ít nhất 1 ảnh (ưu tiên ảnh toàn thân, thấy rõ mặt)");
  if (files.length > 12) return err(422, "tối đa 12 ảnh / người");

  const faceVecs = [], bodyVecs = [];
  let noFace = 0;
  for (const f of files) {
    const [fv, bv] = await Promise.all([
      embedFace(env, tenant, f).catch((e) => { throw Object.assign(new Error("embed face: " + e.message), { status: 502 }); }),
      embedBody(env, tenant, f).catch((e) => { throw Object.assign(new Error("embed body: " + e.message), { status: 502 }); }),
    ]);
    if (fv) faceVecs.push(fv); else noFace++;
    if (bv) bodyVecs.push(bv);
  }
  if (!faceVecs.length && !bodyVecs.length) return err(422, "không trích được embedding nào");

  await sql(env)`INSERT INTO tenants (id,name) VALUES (${tenant},${tenant}) ON CONFLICT (id) DO NOTHING`;
  const pid = genId("p");
  await sql(env)`INSERT INTO persons (id, tenant_id, name, meta)
                 VALUES (${pid}, ${tenant}, ${name}, ${JSON.stringify(meta)}::jsonb)`;
  for (const v of faceVecs)
    await sql(env)`INSERT INTO face_embeddings (id,person_id,tenant_id,vec)
                   VALUES (${genId("fe")}, ${pid}, ${tenant}, ${toVec(v)}::vector)`;
  for (const v of bodyVecs)
    await sql(env)`INSERT INTO body_embeddings (id,person_id,tenant_id,vec)
                   VALUES (${genId("be")}, ${pid}, ${tenant}, ${toVec(v)}::vector)`;

  await Promise.all([
    reloadIndex(env, tenant, "face").catch(() => {}),
    reloadIndex(env, tenant, "body").catch(() => {}),
  ]);
  return json({ person_id: pid, name, face_count: faceVecs.length, body_count: bodyVecs.length, images_without_face: noFace }, 201);
}
