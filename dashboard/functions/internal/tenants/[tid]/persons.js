import { sql, json, err, genId, toVec } from "../../../_lib.js";

// POST /internal/tenants/{tid}/persons  (X-Internal-Key) — enroll từ hệ thống ngoài
// body: { name, person_id?, face_embeddings?: [[512]], body_embeddings?: [[256]] }
export async function onRequestPost({ env, params, request }) {
  const tid = params.tid;
  let b;
  try { b = await request.json(); } catch { return err(400, "body JSON không hợp lệ"); }
  if (!b?.name) return err(422, "cần name");

  const fe = Array.isArray(b.face_embeddings) ? b.face_embeddings : [];
  const be = Array.isArray(b.body_embeddings) ? b.body_embeddings : [];
  for (const v of fe) if (!Array.isArray(v) || v.length !== 512) return err(422, "face_embeddings: mỗi vector 512 chiều");
  for (const v of be) if (!Array.isArray(v) || v.length !== 256) return err(422, "body_embeddings: mỗi vector 256 chiều");
  if (!fe.length && !be.length) return err(422, "cần face_embeddings hoặc body_embeddings");

  await sql(env)`INSERT INTO tenants (id,name) VALUES (${tid},${tid}) ON CONFLICT (id) DO NOTHING`;
  const pid = b.person_id || genId("p");
  await sql(env)`
    INSERT INTO persons (id, tenant_id, name) VALUES (${pid}, ${tid}, ${b.name})
    ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, updated_at=now()`;
  for (const v of fe)
    await sql(env)`INSERT INTO face_embeddings (id,person_id,tenant_id,vec)
                   VALUES (${genId("fe")}, ${pid}, ${tid}, ${toVec(v)}::vector)`;
  for (const v of be)
    await sql(env)`INSERT INTO body_embeddings (id,person_id,tenant_id,vec)
                   VALUES (${genId("be")}, ${pid}, ${tid}, ${toVec(v)}::vector)`;

  const [{ nf }] = await sql(env)`SELECT count(*)::int AS nf FROM face_embeddings WHERE person_id=${pid}`;
  const [{ nb }] = await sql(env)`SELECT count(*)::int AS nb FROM body_embeddings WHERE person_id=${pid}`;
  return json({ person_id: pid, name: b.name, face_count: nf, body_count: nb }, 201);
}
