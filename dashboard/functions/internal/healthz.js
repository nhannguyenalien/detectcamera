import { sql, json } from "../_lib.js";

// GET /internal/healthz
export async function onRequestGet({ env }) {
  try {
    await sql(env)`SELECT 1`;
    return json({ status: "ok", db: "up" });
  } catch (e) {
    return json({ status: "degraded", db: String(e).slice(0, 200) }, 200);
  }
}
