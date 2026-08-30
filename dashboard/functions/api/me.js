import { sql, json, myTenants } from "../_lib.js";

// GET /api/me -> { email, role, tenants:[{id,name, token, usage}] }  (1 call cho portal client)
export async function onRequestGet({ env, data }) {
  const ts = await myTenants(env, data);
  const out = [];
  for (const t of ts) {
    const tok = await sql(env)`
      SELECT token, last_used_at, created_at FROM api_tokens
      WHERE tenant_id=${t.id} AND role='client' AND NOT revoked ORDER BY created_at DESC LIMIT 1`;
    const [u] = await sql(env)`
      SELECT
        count(*) FILTER (WHERE created_at > now() - interval '1 day')::int  AS searches_1d,
        count(*) FILTER (WHERE created_at > now() - interval '30 days')::int AS searches_30d,
        max(created_at) AS last_search
      FROM events WHERE tenant_id=${t.id} AND kind='product_search'`;
    const [p] = await sql(env)`SELECT count(*)::int AS products FROM products WHERE tenant_id=${t.id}`;
    out.push({ ...t, token: tok[0]?.token || null, usage: { ...u, products: p.products } });
  }
  return json({ email: data.email, role: data.role, vision_api_url: env.VISION_API_URL, tenants: out });
}
