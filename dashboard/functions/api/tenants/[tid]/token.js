import { sql, json, err, newToken, assertTenantAccess } from "../../../_lib.js";

// GET /api/tenants/{tid}/token  -> token client đang hiệu lực (tạo nếu chưa có)
export async function onRequestGet({ env, params, data }) {
  const tid = params.tid;
  try { await assertTenantAccess(env, data, tid); } catch (e) { return err(e.status || 403, e.message); }

  let rows = await sql(env)`
    SELECT token, role, label, created_at, last_used_at
    FROM api_tokens WHERE tenant_id=${tid} AND role='client' AND NOT revoked
    ORDER BY created_at DESC`;
  if (!rows.length) {
    const tok = newToken();
    await sql(env)`INSERT INTO api_tokens (token, tenant_id, role, label) VALUES (${tok}, ${tid}, 'client', 'auto')`;
    rows = await sql(env)`SELECT token, role, label, created_at, last_used_at FROM api_tokens WHERE token=${tok}`;
  }
  return json({ tenant_id: tid, tokens: rows });
}

// POST /api/tenants/{tid}/token  { label? }  -> rotate: revoke cũ, tạo mới
export async function onRequestPost({ env, params, request, data }) {
  const tid = params.tid;
  try { await assertTenantAccess(env, data, tid); } catch (e) { return err(e.status || 403, e.message); }
  let b = {};
  try { b = await request.json(); } catch {}
  await sql(env)`UPDATE api_tokens SET revoked=true WHERE tenant_id=${tid} AND role='client' AND NOT revoked`;
  const tok = newToken();
  await sql(env)`INSERT INTO api_tokens (token, tenant_id, role, label) VALUES (${tok}, ${tid}, 'client', ${b.label || "rotated"})`;
  // vision-api sẽ tự nhận token mới ở lần refresh /internal/api-tokens tiếp theo (TTL)
  return json({ tenant_id: tid, token: tok, note: "token cũ đã revoke; vision-api cập nhật sau <=60s" }, 201);
}
