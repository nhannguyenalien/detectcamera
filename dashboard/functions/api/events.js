import { sql, json, err } from "../_lib.js";

// GET /api/events?tenant=&limit=  (admin: all; client: chỉ tenant mình)
export async function onRequestGet({ env, request, data }) {
  const u = new URL(request.url);
  const lim = Math.min(200, Math.max(1, +(u.searchParams.get("limit") || 50)));
  let tenant = u.searchParams.get("tenant") || null;

  if (data.role !== "admin") {
    const mine = await sql(env)`SELECT id FROM tenants WHERE lower(owner_email)=${data.email.toLowerCase()}`;
    const ids = mine.map((r) => r.id);
    if (!ids.length) return json({ events: [] });
    if (tenant && !ids.includes(tenant)) return err(403, "không có quyền");
    if (!tenant) {
      const rows = await sql(env)`
        SELECT id,tenant_id,kind,payload,created_at FROM events
        WHERE tenant_id = ANY(${ids}) ORDER BY created_at DESC LIMIT ${lim}`;
      return json({ events: rows });
    }
  }
  const rows = tenant
    ? await sql(env)`SELECT id,tenant_id,kind,payload,created_at FROM events WHERE tenant_id=${tenant} ORDER BY created_at DESC LIMIT ${lim}`
    : await sql(env)`SELECT id,tenant_id,kind,payload,created_at FROM events ORDER BY created_at DESC LIMIT ${lim}`;
  return json({ events: rows });
}
