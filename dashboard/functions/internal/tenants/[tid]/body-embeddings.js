import { sql, json, err, fromVec } from "../../../_lib.js";

// GET /internal/tenants/{tid}/body-embeddings  -> vision-api dựng FAISS body (person ReID)
// person_id CHUNG với face -> fusion /v1/persons/identify gộp được.
export async function onRequestGet({ env, params }) {
  const tid = params.tid;
  const t = await sql(env)`SELECT 1 FROM tenants WHERE id=${tid}`;
  if (!t.length) return err(404, "tenant không tồn tại");

  const rows = await sql(env)`
    SELECT p.id AS person_id, p.name,
           coalesce(
             json_agg(e.vec::text ORDER BY e.created_at) FILTER (WHERE e.id IS NOT NULL),
             '[]'
           ) AS vecs
    FROM persons p
    LEFT JOIN body_embeddings e ON e.person_id = p.id
    WHERE p.tenant_id = ${tid}
    GROUP BY p.id
    ORDER BY p.created_at`;

  const persons = rows
    .map((r) => ({
      person_id: r.person_id,
      name: r.name,
      embeddings: (r.vecs || []).map((v) => fromVec(v)),
    }))
    .filter((p) => p.embeddings.length);

  return json({ tenant_id: tid, dim: 256, model: "person-reidentification-retail-0277", persons });
}
