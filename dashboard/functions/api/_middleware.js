// Bảo vệ /api/* : chấp nhận (a) Cloudflare Access header, hoặc (b) session cookie từ /api/login.
// /api/login và /api/logout là public.
import { verifySession, readCookie } from "../_auth.js";

export async function onRequest({ request, env, next, data }) {
  const url = new URL(request.url);
  if (url.pathname === "/api/login" || url.pathname === "/api/logout") return next();

  const admins = (env.ADMIN_EMAILS || "").toLowerCase().split(",").map((s) => s.trim()).filter(Boolean);

  // (a) Cloudflare Access
  let email =
    request.headers.get("Cf-Access-Authenticated-User-Email") ||
    request.headers.get("cf-access-authenticated-user-email");
  let role = null;

  // (b) session cookie
  if (!email && env.SESSION_SECRET) {
    const s = await verifySession(env.SESSION_SECRET, readCookie(request, "sess"));
    if (s) { email = s.email; role = s.role; data.tenant = s.tenant || null; }
  }

  // (c) dev
  if (!email && env.ALLOW_INSECURE === "1") { email = env.DEV_EMAIL || "dev@local"; }

  if (!email) {
    return new Response(JSON.stringify({ error: "Chưa đăng nhập", need_login: true }), {
      status: 401, headers: { "content-type": "application/json" },
    });
  }
  data.email = email;
  data.role = role || (admins.includes(email.toLowerCase()) ? "admin" : "client");

  const res = await next();
  res.headers.set("X-User", email);
  res.headers.set("X-Role", data.role);
  return res;
}
