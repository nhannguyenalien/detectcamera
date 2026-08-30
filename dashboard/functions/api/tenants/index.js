import { sql, json, err, genId, myTenants } from "../../_lib.js";

// GET /api/tenants  -> danh sách tenant (admin: tất cả; client: của mình) kèm số sp + usage 30 ngày
export async function onRequestGet({ env, data }) {
  const ts = await myTenants(env, data);
  if (!ts.length) return json({ tenants: [] });
  const ids = ts.map((t) => t.id);
  const prod = await sql(env)`
    SELECT tenant_id, count(*)::int AS n FROM products WHERE tenant_id = ANY(${ids}) GROUP BY tenant_id`;
  const usage = await sql(env)`
    SELECT tenant_id, count(*)::int AS n FROM events
    WHERE tenant_id = ANY(${ids}) AND created_at > now() - interval '30 days' GROUP BY tenant_id`;
  const pm = Object.fromEntries(prod.map((r) => [r.tenant_id, r.n]));
  const um = Object.fromEntries(usage.map((r) => [r.tenant_id, r.n]));
  return json({
    role: data.role,
    tenants: ts.map((t) => ({ ...t, products: pm[t.id] || 0, searches_30d: um[t.id] || 0 })),
  });
}

// POST /api/tenants  { id, name, owner_email }  -> tạo tenant (admin)
export async function onRequestPost({ env, request, data }) {
  if (data.role !== "admin") return err(403, "chỉ admin");
  let b;
  try { b = await request.json(); } catch { return err(400, "body JSON không hợp lệ"); }
  const id = (b.id || genId("t")).trim();
  if (!/^[\w-]{2,40}$/.test(id)) return err(422, "id chỉ chữ/số/_/- (2-40 ký tự)");
  if (!b.name) return err(422, "cần name");
  await sql(env)`
    INSERT INTO tenants (id, name, owner_email) VALUES (${id}, ${b.name}, ${b.owner_email || null})
    ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, owner_email=EXCLUDED.owner_email`;
  return json({ id, name: b.name, owner_email: b.owner_email || null }, 201);
}
