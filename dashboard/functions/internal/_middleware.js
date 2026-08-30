// /internal/* = backend cho vision-api gọi (machine-to-machine).
// KHÔNG để Cloudflare Access chặn path này (exclude /internal/* trong Access app).
// Bảo vệ bằng X-Internal-Key.
export async function onRequest({ request, env, next }) {
  const key = request.headers.get("X-Internal-Key");
  if (!env.INTERNAL_KEY || key !== env.INTERNAL_KEY) {
    return new Response(JSON.stringify({ error: "X-Internal-Key sai hoặc thiếu" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  }
  return next();
}
