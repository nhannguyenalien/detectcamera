// Password hashing (PBKDF2-SHA256) + session token (HMAC-SHA256) — chạy trên Web Crypto.
const enc = new TextEncoder();
const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const b64u = (s) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s) => atob(s.replace(/-/g, "+").replace(/_/g, "/"));
const fromB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

const ITER = 100000; // Cloudflare Workers PBKDF2 cap

export async function hashPassword(pw) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", enc.encode(pw), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations: ITER, hash: "SHA-256" }, key, 256);
  return `pbkdf2$${ITER}$${b64(salt)}$${b64(bits)}`;
}

export async function verifyPassword(pw, stored) {
  try {
    const [alg, iter, saltB64, hashB64] = stored.split("$");
    if (alg !== "pbkdf2") return false;
    const salt = fromB64(saltB64);
    const key = await crypto.subtle.importKey("raw", enc.encode(pw), "PBKDF2", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations: +iter, hash: "SHA-256" }, key, 256);
    return b64(bits) === hashB64;
  } catch { return false; }
}

async function hmac(secret, msg) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
}

export async function signSession(secret, payload, ttlSec = 86400) {
  const body = { ...payload, exp: Math.floor(Date.now() / 1000) + ttlSec };
  const p = b64u(JSON.stringify(body));
  const sig = b64u(await hmac(secret, p));
  return `${p}.${sig}`;
}

export async function verifySession(secret, token) {
  if (!token || !token.includes(".")) return null;
  const [p, sig] = token.split(".");
  const good = b64u(await hmac(secret, p));
  if (sig !== good) return null;
  let body;
  try { body = JSON.parse(unb64u(p)); } catch { return null; }
  if (!body.exp || body.exp < Math.floor(Date.now() / 1000)) return null;
  return body; // { email, role, tenant, exp }
}

export const readCookie = (req, name) => {
  const m = (req.headers.get("Cookie") || "").match(new RegExp("(?:^|; )" + name + "=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
};
export const setCookie = (name, val, maxAge) =>
  `${name}=${encodeURIComponent(val)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
