import { sql, json, err } from "../_lib.js";
import { hashPassword } from "../_auth.js";

// GET /api/users  (admin) -> danh sách user
export async function onRequestGet({ env, data }) {
  if (data.role !== "admin") return err(403, "chỉ admin");
  const rows = await sql(env)`SELECT email, role, tenant_id, created_at, last_login_at FROM users ORDER BY created_at`;
  return json({ users: rows });
}

// POST /api/users  { email, password, role?, tenant_id? }  (admin) -> tạo/đổi mật khẩu
export async function onRequestPost({ env, request, data }) {
  if (data.role !== "admin") return err(403, "chỉ admin");
  let b;
  try { b = await request.json(); } catch { return err(400, "body JSON không hợp lệ"); }
  const email = String(b.email || "").trim().toLowerCase();
  const pw = String(b.password || "");
  const role = b.role === "admin" ? "admin" : "client";
  if (!email || pw.length < 8) return err(422, "cần email + password >= 8 ký tự");
  const hash = await hashPassword(pw);
  await sql(env)`
    INSERT INTO users (email, password_hash, role, tenant_id)
    VALUES (${email}, ${hash}, ${role}, ${b.tenant_id || null})
    ON CONFLICT (email) DO UPDATE SET password_hash=EXCLUDED.password_hash, role=EXCLUDED.role, tenant_id=EXCLUDED.tenant_id`;
  return json({ ok: true, email, role, tenant_id: b.tenant_id || null }, 201);
}

// DELETE /api/users?email=  (admin)
export async function onRequestDelete({ env, request, data }) {
  if (data.role !== "admin") return err(403, "chỉ admin");
  const email = new URL(request.url).searchParams.get("email");
  const r = await sql(env)`DELETE FROM users WHERE lower(email)=${String(email || "").toLowerCase()} RETURNING email`;
  return json({ deleted: r.length });
}
