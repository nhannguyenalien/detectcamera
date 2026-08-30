import { sql, json } from "../_lib.js";

// POST /internal/events  <- vision-api ghi audit mỗi lần search (fire-and-forget)
export async function onRequestPost({ env, request }) {
  let b = {};
  try { b = await request.json(); } catch {}
  await sql(env)`
    INSERT INTO events (tenant_id, kind, payload)
    VALUES (${b.tenant_id || null}, ${b.kind || "search"}, ${JSON.stringify(b.payload || {})}::jsonb)`;
  return json({ ok: true }, 201);
}

// GET /internal/events?tenant_id=&limit=  (debug)
export async function onRequestGet({ env, request }) {
  const u = new URL(request.url);
  const t = u.searchParams.get("tenant_id");
  const lim = Math.min(500, Math.max(1, +(u.searchParams.get("limit") || 50)));
  const rows = t
    ? await sql(env)`SELECT id,tenant_id,kind,payload,created_at FROM events WHERE tenant_id=${t} ORDER BY created_at DESC LIMIT ${lim}`
    : await sql(env)`SELECT id,tenant_id,kind,payload,created_at FROM events ORDER BY created_at DESC LIMIT ${lim}`;
  return json({ events: rows });
}
