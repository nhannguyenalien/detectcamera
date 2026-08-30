import { sql, json } from "../../../../_lib.js";

// DELETE /internal/tenants/{tid}/products/{pid}
export async function onRequestDelete({ env, params }) {
  const rows = await sql(env)`
    DELETE FROM products WHERE id=${params.pid} AND tenant_id=${params.tid} RETURNING id`;
  return json({ deleted: rows.length });
}
