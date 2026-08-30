import { sql, json } from "../_lib.js";

// GET /internal/api-tokens  -> map token cho vision-api xác thực client.
// vision-api merge với VISION_API_TOKENS (env) + refresh theo TTL.
export async function onRequestGet({ env }) {
  const rows = await sql(env)`
    SELECT token, tenant_id, role FROM api_tokens WHERE NOT revoked`;
  const tokens = {};
  for (const r of rows) tokens[r.token] = { tenant: r.tenant_id, role: r.role };
  return json({ tokens });
}
