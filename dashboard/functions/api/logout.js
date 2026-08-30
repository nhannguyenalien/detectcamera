import { json } from "../_lib.js";
import { setCookie } from "../_auth.js";

export async function onRequest() {
  return json({ ok: true }, 200, { "Set-Cookie": setCookie("sess", "", 0) });
}
