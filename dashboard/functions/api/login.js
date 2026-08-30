import { sql, json, err } from "../_lib.js";
import { verifyPassword, signSession, setCookie } from "../_auth.js";

// POST /api/login  { email, password }  -> set cookie 'sess'
export async function onRequestPost({ env, request }) {
  if (!env.SESSION_SECRET) return err(500, "SESSION_SECRET chưa cấu hình");
  let b;
  try { b = await request.json(); } catch { return err(400, "body JSON không hợp lệ"); }
  const email = String(b.email || "").trim().toLowerCase();
  const pw = String(b.password || "");
  if (!email || !pw) return err(422, "cần email + password");

  const rows = await sql(env)`SELECT email, password_hash, role, tenant_id FROM users WHERE lower(email)=${email}`;
  if (!rows.length || !(await verifyPassword(pw, rows[0].password_hash)))
    return err(401, "Sai email hoặc mật khẩu");

  const u = rows[0];
  await sql(env)`UPDATE users SET last_login_at=now() WHERE email=${u.email}`;
  const tok = await signSession(env.SESSION_SECRET, { email: u.email, role: u.role, tenant: u.tenant_id });
  return json({ ok: true, email: u.email, role: u.role }, 200, { "Set-Cookie": setCookie("sess", tok, 86400) });
}

export async function onRequestGet() {
  return json({ ok: true, hint: "POST { email, password }" });
}
