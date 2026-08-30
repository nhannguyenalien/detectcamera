import { sql, json } from "../_lib.js";

// GET /internal/tenants  -> vision-api dựng index cho từng tenant
export async function onRequestGet({ env }) {
  const rows = await sql(env)`SELECT id, name FROM tenants ORDER BY created_at`;
  return json({ tenants: rows });
}
