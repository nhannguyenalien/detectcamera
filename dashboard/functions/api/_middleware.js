// Bảo vệ /api/*: dựa vào Cloudflare Access. Xác định role admin vs client theo email.
// Local dev (không có Access): đặt ALLOW_INSECURE=1 + DEV_EMAIL=... trong .dev.vars
export async function onRequest({ request, env, next, data }) {
  let email =
    request.headers.get("Cf-Access-Authenticated-User-Email") ||
    request.headers.get("cf-access-authenticated-user-email");

  if (!email && env.ALLOW_INSECURE === "1") email = env.DEV_EMAIL || "dev@local";
  if (!email) {
    return new Response(JSON.stringify({ error: "Chưa đăng nhập (Cloudflare Access)" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  }

  const admins = (env.ADMIN_EMAILS || "")
    .toLowerCase()
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  data.email = email;
  data.role = admins.includes(email.toLowerCase()) ? "admin" : "client";

  const res = await next();
  res.headers.set("X-User", email);
  res.headers.set("X-Role", data.role);
  return res;
}
