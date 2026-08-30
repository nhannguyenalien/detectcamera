const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let ME = { role: "client", tenants: [] };
let TENANT = null;
let prodPage = 1;

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, opts);
  const t = await r.text();
  let d;
  try { d = JSON.parse(t); } catch { d = t; }
  if (!r.ok) throw new Error(d?.error || d || r.status);
  return d;
}
function flash(msg, kind = "danger") {
  $("#alert").innerHTML = `<div class="alert alert-${kind} alert-dismissible">${esc(msg)}<a class="btn-close" data-bs-dismiss="alert"></a></div>`;
  if (kind === "success") setTimeout(() => ($("#alert").innerHTML = ""), 3000);
}
const modal = (id, show) => bootstrap.Modal.getOrCreateInstance($("#" + id))[show ? "show" : "hide"]();

// ---------- login ----------
function showLogin(msg) {
  $("#appScreen").hidden = true;
  $("#loginScreen").hidden = false;
  if (msg) { $("#loginErr").hidden = false; $("#loginErr").textContent = msg; }
}
async function doLogin() {
  $("#loginErr").hidden = true;
  const email = $("#liEmail").value.trim(), password = $("#liPass").value;
  if (!email || !password) return showLogin("Nhập email và mật khẩu");
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password }) });
    const d = await r.json();
    if (!r.ok) return showLogin(d.error || "Đăng nhập lỗi");
    location.reload();
  } catch (e) { showLogin(String(e)); }
}
$("#liBtn").onclick = doLogin;
$("#liPass").addEventListener("keydown", (e) => e.key === "Enter" && doLogin());

// ---------- boot ----------
(async function boot() {
  try {
    ME = await api("/me");
  } catch (e) {
    return showLogin(/Chưa đăng nhập/.test(e.message) ? null : e.message);
  }
  $("#loginScreen").hidden = true;
  $("#appScreen").hidden = false;
  $("#btnLogout").hidden = false;
  $("#btnLogout").onclick = async () => { await fetch("/api/logout"); location.reload(); };
  $("#whoami").textContent = `${ME.email} · ${ME.role}`;
  if (ME.role !== "admin") $$("[data-admin]").forEach((el) => (el.hidden = true));

  const tsel = $("#tenantSel");
  const list = ME.role === "admin" ? (await api("/tenants")).tenants : ME.tenants;
  tsel.innerHTML = list.map((t) => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.id)})</option>`).join("") || "<option>—</option>";
  TENANT = list[0]?.id || null;
  tsel.onchange = () => { TENANT = tsel.value; refreshTab(); };

  $$("#tabs .nav-link").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      $$("#tabs .nav-link").forEach((x) => x.classList.remove("active"));
      a.classList.add("active");
      $$("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== a.dataset.tab));
      refreshTab(a.dataset.tab);
    })
  );
  wire();
  refreshTab("system");
})();

function curTab() { return $("#tabs .nav-link.active")?.dataset.tab; }
function refreshTab(tab = curTab()) {
  if (tab === "system") loadHealth();
  if (tab === "products") { prodPage = 1; loadProducts(); }
  if (tab === "search") $("#searchOut").textContent = "…";
  if (tab === "clients") loadTenants();
  if (tab === "portal") loadPortal();
  if (tab === "events") loadEvents();
}

// ---------- system ----------
async function loadHealth() {
  $("#healthOut").textContent = "…";
  try { $("#healthOut").textContent = JSON.stringify(await api("/health"), null, 2); }
  catch (e) { $("#healthOut").textContent = "lỗi: " + e.message; }
}

// ---------- products ----------
async function loadProducts() {
  if (!TENANT) return;
  const q = encodeURIComponent($("#pq").value.trim());
  const d = await api(`/products?tenant=${TENANT}&page=${prodPage}&per=50&q=${q}`).catch((e) => (flash(e.message), null));
  if (!d) return;
  $("#prodCount").textContent = `${d.total} sản phẩm · trang ${d.page}`;
  $("#prodRows").innerHTML = d.products.map((p) => `
    <tr>
      <td class="mono">${esc(p.sku || "")}</td>
      <td>${esc(p.name)}</td>
      <td>${p.n_emb}</td>
      <td class="text-secondary">${new Date(p.created_at).toLocaleString()}</td>
      <td><button class="btn btn-sm btn-ghost-danger" data-del="${esc(p.id)}">Xoá</button></td>
    </tr>`).join("") || `<tr><td colspan="5" class="text-secondary">chưa có sản phẩm</td></tr>`;
  $$("#prodRows [data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Xoá sản phẩm này?")) return;
    await api("/products/" + b.dataset.del, { method: "DELETE" }).catch((e) => flash(e.message));
    loadProducts();
  }));
}

// ---------- clients ----------
async function loadTenants() {
  const d = await api("/tenants").catch((e) => (flash(e.message), null));
  if (!d) return;
  $("#tenantRows").innerHTML = d.tenants.map((t) => `
    <tr><td class="mono">${esc(t.id)}</td><td>${esc(t.name)}</td>
    <td class="text-secondary">${esc(t.owner_email || "")}</td>
    <td>${t.products}</td><td>${t.searches_30d}</td></tr>`).join("");
}

// ---------- portal (token + usage) ----------
async function loadPortal() {
  const me = await api("/me");
  $("#portalWrap").innerHTML = me.tenants.map((t) => `
    <div class="card mb-3"><div class="card-header"><h3 class="card-title">${esc(t.name)} <span class="text-secondary mono">${esc(t.id)}</span></h3></div>
    <div class="card-body">
      <label class="form-label">API token (role client)</label>
      <div class="input-group mb-2"><input class="form-control mono token-box" readonly value="${esc(t.token || "—")}">
        <button class="btn" data-copy="${esc(t.token || "")}">Copy</button>
        <button class="btn btn-outline-danger" data-rotate="${esc(t.id)}">Rotate</button></div>
      <div class="text-secondary small mb-3">Gọi vision-api: header <span class="mono">Authorization: Bearer &lt;token&gt;</span> +
        <span class="mono">X-Tenant-ID: ${esc(t.id)}</span> · endpoint <span class="mono">${esc(me.vision_api_url)}/v1/products/search</span></div>
      <div class="row g-2">
        <div class="col"><div class="card card-sm"><div class="card-body text-center"><div class="h1 m-0">${t.usage.products}</div><div class="text-secondary">sản phẩm</div></div></div></div>
        <div class="col"><div class="card card-sm"><div class="card-body text-center"><div class="h1 m-0">${t.usage.searches_1d}</div><div class="text-secondary">search / 24h</div></div></div></div>
        <div class="col"><div class="card card-sm"><div class="card-body text-center"><div class="h1 m-0">${t.usage.searches_30d}</div><div class="text-secondary">search / 30d</div></div></div></div>
        <div class="col"><div class="card card-sm"><div class="card-body text-center"><div class="small m-0">${t.usage.last_search ? new Date(t.usage.last_search).toLocaleString() : "—"}</div><div class="text-secondary">lần cuối</div></div></div></div>
      </div>
    </div></div>`).join("") || `<div class="text-secondary">Chưa có tenant nào gắn với email của bạn. Nhờ admin thêm ở tab Clients (Email chủ = email này).</div>`;
  $$("#portalWrap [data-copy]").forEach((b) => (b.onclick = () => { navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Đã copy"; }));
  $$("#portalWrap [data-rotate]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Tạo token mới và vô hiệu token cũ? App đang dùng token cũ sẽ hỏng.")) return;
    const r = await api(`/tenants/${b.dataset.rotate}/token`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }).catch((e) => (flash(e.message), null));
    if (r) { flash("Token mới: " + r.token + " (vision-api cập nhật trong <=60s)", "success"); loadPortal(); }
  }));
}

// ---------- events ----------
async function loadEvents() {
  $("#eventsOut").textContent = "…";
  try {
    const d = await api("/events?limit=50" + (TENANT ? "&tenant=" + TENANT : ""));
    $("#eventsOut").textContent = JSON.stringify(d.events, null, 2);
  } catch (e) { $("#eventsOut").textContent = "lỗi: " + e.message; }
}

// ---------- wire buttons/modals ----------
function wire() {
  $("#btnHealth").onclick = loadHealth;
  $("#btnReload") && ($("#btnReload").onclick = async () => {
    try { await api(`/reload?tenant=${TENANT}`, { method: "POST" }); flash("Đã reload index cho " + TENANT, "success"); }
    catch (e) { flash(e.message); }
  });
  $("#pq").oninput = () => { prodPage = 1; loadProducts(); };
  $("#pPrev").onclick = () => { if (prodPage > 1) { prodPage--; loadProducts(); } };
  $("#pNext").onclick = () => { prodPage++; loadProducts(); };

  $("#btnAddProd").onclick = () => { ["npName", "npSku", "npMeta"].forEach((i) => ($("#" + i).value = "")); $("#npImgs").value = ""; modal("mProd", true); };
  $("#npSave").onclick = async () => {
    const fd = new FormData();
    fd.append("tenant", TENANT);
    fd.append("name", $("#npName").value.trim());
    fd.append("sku", $("#npSku").value.trim());
    fd.append("meta", $("#npMeta").value.trim() || "{}");
    [...$("#npImgs").files].forEach((f) => fd.append("images", f));
    $("#npSave").disabled = true;
    try { const r = await api("/products", { method: "POST", body: fd }); modal("mProd", false); flash(`Đã thêm ${r.name} (${r.embedding_count} ảnh)`, "success"); loadProducts(); }
    catch (e) { flash(e.message); }
    finally { $("#npSave").disabled = false; }
  };

  $("#btnAddTenant") && ($("#btnAddTenant").onclick = () => { ["ntId", "ntName", "ntEmail", "ntPass"].forEach((i) => ($("#" + i).value = "")); modal("mTenant", true); });
  $("#ntSave") && ($("#ntSave").onclick = async () => {
    try {
      const r = await api("/tenants", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: $("#ntId").value.trim(), name: $("#ntName").value.trim(), owner_email: $("#ntEmail").value.trim(), password: $("#ntPass").value }) });
      modal("mTenant", false);
      flash("Đã tạo client" + (r.login ? ` · login: ${r.login}` : ""), "success");
      boot2();
    } catch (e) { flash(e.message); }
  });

  $("#btnSearch").onclick = async () => {
    const f = $("#sImg").files[0];
    if (!f) return flash("Chọn ảnh");
    const fd = new FormData(); fd.append("tenant", TENANT); fd.append("image", f);
    if ($("#sThr").value.trim()) fd.append("threshold", $("#sThr").value.trim());
    $("#searchOut").textContent = "đang nhận diện…";
    try { $("#searchOut").textContent = JSON.stringify(await api("/search", { method: "POST", body: fd }), null, 2); }
    catch (e) { $("#searchOut").textContent = "lỗi: " + e.message; }
  };

  [...$$("[data-close]")].forEach((b) => (b.onclick = () => { modal("mProd", false); modal("mTenant", false); }));
  document.querySelectorAll(".btn-close[data-bs-dismiss]").length; // tabler handles
}
async function boot2() {
  const list = ME.role === "admin" ? (await api("/tenants")).tenants : (await api("/me")).tenants;
  $("#tenantSel").innerHTML = list.map((t) => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.id)})</option>`).join("");
  if (curTab() === "clients") loadTenants();
}
