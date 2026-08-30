import { json, err, reloadIndex, assertTenantAccess } from "../_lib.js";

// POST /api/reload?tenant=t_demo  -> rebuild FAISS product cho tenant
export async function onRequestPost({ env, request, data }) {
  const tenant = new URL(request.url).searchParams.get("tenant") || env.DEFAULT_TENANT;
  try {
    await assertTenantAccess(env, data, tenant);
    const r = await reloadIndex(env, tenant);
    return json({ ok: true, result: r });
  } catch (e) {
    return err(e.status || 502, String(e.message || e));
  }
}
