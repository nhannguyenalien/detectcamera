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
  if (tab === "search") $("#searchResult").innerHTML = `<div class="text-secondary">Chọn ảnh rồi bấm Nhận diện.</div>`;
  if (tab === "clients") loadTenants();
  if (tab === "portal") loadPortal();
  if (tab === "events") loadEvents();
}

// ---------- system ----------
function statCard(title, value, sub) {
  return `<div class="col-6 col-md-3"><div class="card card-sm"><div class="card-body">
    <div class="text-secondary">${esc(title)}</div>
    <div class="h2 m-0">${esc(value)}</div>
    <div class="text-secondary small">${esc(sub || "")}</div></div></div></div>`;
}
async function loadHealth() {
  $("#healthOut").textContent = "…";
  $("#statCards").innerHTML = "";
  try {
    const d = await api("/health");
    const r = d.ready || {}, g = d.gpu || {};
    const face = r.modalities?.face || {}, prod = r.modalities?.product || {};
    $("#statCards").innerHTML =
      statCard("vision-api", r.ready ? "✅ ready" : "⏳ " + (r.detail || "?"), d.vision_api_url) +
      statCard("GPU", g.name || "—", g.vram_used_mb != null ? `${g.vram_used_mb} / ${g.vram_total_mb} MB` : "") +
      statCard("Face model", face.enabled ? face.model : "tắt", face.provider || "") +
      statCard("Product model", prod.enabled ? prod.model : "tắt", prod.provider || "");
    $("#healthOut").textContent = JSON.stringify(d, null, 2);
  } catch (e) {
    $("#statCards").innerHTML = statCard("Lỗi", "—", e.message);
    $("#healthOut").textContent = "lỗi: " + e.message;
  }
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
    </tr>`).join("") ||
    `<tr><td colspan="5" class="text-secondary py-4 text-center">Chưa có sản phẩm.
      Bấm <b>+ Thêm sản phẩm</b> (upload ảnh) hoặc <b>Nhập hàng loạt</b> (theo URL).</td></tr>`;
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
    $("#searchResult").innerHTML = `<div class="text-secondary">đang nhận diện…</div>`;
    try {
      const d = await api("/search", { method: "POST", body: fd });
      const row = (c, best) => `
        <div class="card card-sm mb-2 ${best ? "border-primary" : ""}"><div class="card-body d-flex align-items-center">
          <div class="flex-fill"><div class="fw-bold">${esc(c.name || c.product_id)}</div>
            <div class="text-secondary small mono">${esc(c.sku || "")} · ${esc(c.product_id)}</div></div>
          <div class="text-end"><span class="badge ${c.score >= d.threshold ? "bg-green" : "bg-secondary"}">${(c.score * 100).toFixed(1)}%</span></div>
        </div></div>`;
      $("#searchResult").innerHTML =
        `<div class="mb-2">${d.match
          ? `<span class="status status-green">Khớp: <b class="ms-1">${esc(d.match.name)}</b> · ${(d.match.score * 100).toFixed(1)}%</span>`
          : `<span class="status status-secondary">Không khớp (ngưỡng ${d.threshold})</span>`}
          <span class="text-secondary small ms-2">${d.inference_ms} ms · ${d.index?.products || 0} sp trong index</span></div>` +
        (d.candidates || []).map((c) => row(c, d.match && c.product_id === d.match.product_id)).join("") ||
        `<div class="text-secondary">Không có candidate nào.</div>`;
    } catch (e) { $("#searchResult").innerHTML = `<div class="text-danger">lỗi: ${esc(e.message)}</div>`; }
  };

  $("#btnBulk") && ($("#btnBulk").onclick = () => { $("#bulkText").value = ""; $("#bulkOut").innerHTML = ""; modal("mBulk", true); });
  $("#bulkRun") && ($("#bulkRun").onclick = async () => {
    const items = $("#bulkText").value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [name, sku, urls] = l.split("|").map((x) => (x || "").trim());
      return { name, sku: sku || null, image_urls: (urls || "").split(",").map((u) => u.trim()).filter(Boolean) };
    }).filter((it) => it.name && it.image_urls.length);
    if (!items.length) return flash("Không có dòng hợp lệ (cần: Tên | SKU | url)");
    $("#bulkOut").innerHTML = `đang xử lý ${items.length} sản phẩm…`;
    $("#bulkRun").disabled = true;
    try {
      const r = await api("/products/bulk", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ tenant: TENANT, items }) });
      $("#bulkOut").innerHTML = `<b>Thêm ${r.added}</b>, lỗi ${r.failed}.<br>` +
        r.results.map((x) => `${x.ok ? "✅" : "❌"} ${esc(x.name)} ${x.ok ? `(${x.embeddings} ảnh)` : "— " + esc(x.error)}`).join("<br>");
      loadProducts();
    } catch (e) { $("#bulkOut").innerHTML = `<span class="text-danger">${esc(e.message)}</span>`; }
    finally { $("#bulkRun").disabled = false; }
  });

  [...$$("[data-close]")].forEach((b) => (b.onclick = () => { modal("mProd", false); modal("mTenant", false); modal("mBulk", false); }));
  document.querySelectorAll(".btn-close[data-bs-dismiss]").length; // tabler handles
}
async function boot2() {
  const list = ME.role === "admin" ? (await api("/tenants")).tenants : (await api("/me")).tenants;
  $("#tenantSel").innerHTML = list.map((t) => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.id)})</option>`).join("");
  if (curTab() === "clients") loadTenants();
}
