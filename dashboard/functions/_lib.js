// Tiện ích dùng chung. File _prefix -> không thành route.
import { neon } from "@neondatabase/serverless";

export const sql = (env) => neon(env.DATABASE_URL);

export const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });

export const err = (status, msg) => json({ error: msg }, status);

export const genId = (p = "id") =>
  p + "_" + crypto.randomUUID().replace(/-/g, "").slice(0, 14);

export const newToken = () =>
  "tok_" + [...crypto.getRandomValues(new Uint8Array(20))].map((b) => b.toString(16).padStart(2, "0")).join("");

// pgvector: mảng số -> literal "[a,b,c]"; text pgvector là JSON hợp lệ nên parse ngược được
export const toVec = (arr) => "[" + arr.map(Number).join(",") + "]";
export const fromVec = (v) => (typeof v === "string" ? JSON.parse(v) : v);

// admin thao tác được mọi tenant; client chỉ tenant mình sở hữu (owner_email)
export async function assertTenantAccess(env, data, tenantId) {
  if (data.role === "admin") return true;
  const rows = await sql(env)`SELECT 1 FROM tenants WHERE id=${tenantId} AND lower(owner_email)=${data.email.toLowerCase()}`;
  if (!rows.length) throw Object.assign(new Error("Không có quyền với tenant này"), { status: 403 });
  return true;
}

export async function myTenants(env, data) {
  if (data.role === "admin")
    return sql(env)`SELECT id,name,owner_email,created_at FROM tenants ORDER BY created_at`;
  return sql(env)`SELECT id,name,owner_email,created_at FROM tenants WHERE lower(owner_email)=${data.email.toLowerCase()} ORDER BY created_at`;
}

// ---- gọi vision-api ----
export async function vision(env, path, { method = "GET", tenant, body, admin = false } = {}) {
  const headers = {
    Authorization: `Bearer ${admin ? env.VISION_ADMIN_TOKEN : env.VISION_CLIENT_TOKEN}`,
  };
  if (tenant) headers["X-Tenant-ID"] = tenant;
  const r = await fetch(env.VISION_API_URL.replace(/\/$/, "") + path, { method, headers, body });
  const text = await r.text();
  let d;
  try { d = JSON.parse(text); } catch { d = text; }
  if (!r.ok) throw new Error(`vision-api ${path} -> ${r.status}: ${String(text).slice(0, 300)}`);
  return d;
}

export async function embedImage(env, tenant, fileBlob) {
  const fd = new FormData();
  fd.append("file", fileBlob, "img.jpg");
  const d = await vision(env, "/v1/products/embed", { method: "POST", tenant, body: fd });
  if (!Array.isArray(d.embedding)) throw new Error("vision-api không trả embedding");
  return d.embedding;
}

export const reloadIndex = (env, tenant) =>
  vision(env, `/admin/reload?modality=product&tenant_id=${encodeURIComponent(tenant)}`, {
    method: "POST",
    admin: true,
  });
