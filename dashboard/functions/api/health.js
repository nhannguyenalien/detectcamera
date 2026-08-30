import { json, err, vision } from "../_lib.js";

// GET /api/health -> trạng thái vision-api (admin)
export async function onRequestGet({ env, data }) {
  if (data.role !== "admin") return err(403, "chỉ admin");
  try {
    const [ready, gpu] = await Promise.all([
      vision(env, "/ready").catch((e) => ({ ready: false, detail: String(e) })),
      vision(env, "/gpu").catch(() => null),
    ]);
    return json({ vision_api_url: env.VISION_API_URL, ready, gpu });
  } catch (e) {
    return err(502, String(e));
  }
}
