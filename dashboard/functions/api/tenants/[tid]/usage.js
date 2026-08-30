import { sql, json, err, assertTenantAccess } from "../../../_lib.js";

// GET /api/tenants/{tid}/usage  -> thống kê usage từ bảng events
export async function onRequestGet({ env, params, data }) {
  const tid = params.tid;
  try { await assertTenantAccess(env, data, tid); } catch (e) { return err(e.status || 403, e.message); }

  const [totals] = await sql(env)`
    SELECT
      count(*) FILTER (WHERE created_at > now() - interval '1 day')::int   AS searches_1d,
      count(*) FILTER (WHERE created_at > now() - interval '7 days')::int   AS searches_7d,
      count(*) FILTER (WHERE created_at > now() - interval '30 days')::int  AS searches_30d,
      count(*) FILTER (WHERE (payload->'match') IS NOT NULL
                        AND payload->>'match' <> 'null'
                        AND created_at > now() - interval '30 days')::int   AS matches_30d,
      max(created_at) AS last_search
    FROM events WHERE tenant_id=${tid} AND kind='product_search'`;

  const daily = await sql(env)`
    SELECT date_trunc('day', created_at)::date AS day, count(*)::int AS n
    FROM events WHERE tenant_id=${tid} AND kind='product_search'
      AND created_at > now() - interval '30 days'
    GROUP BY 1 ORDER BY 1`;

  const [pc] = await sql(env)`SELECT count(*)::int AS products FROM products WHERE tenant_id=${tid}`;
  return json({ tenant_id: tid, products: pc.products, ...totals, daily });
}
