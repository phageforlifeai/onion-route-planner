/* Onion Route Planner – front-end logic (vanilla JS + Leaflet) */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Math.random().toString(36).slice(2, 10);
const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD local
const num = v => (v === "" || v == null || isNaN(+v)) ? null : +v;

const S = { settings: null, customers: [], orders: [], date: today(), result: null,
            map: null, layers: [], legend: null, pick: null, saveTimer: null };

async function api(path, method = "GET", body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.blob();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
}
const custById = id => S.customers.find(c => c.id === id);
const sortedCustomers = () => [...S.customers].sort((a, b) => a.name.localeCompare(b.name));

/* =============================== MOBILE VIEWS =============================== */
const isMobile = () => window.matchMedia("(max-width: 760px)").matches;
function setView(v) {
  document.body.dataset.view = v;
  $$(".mnav button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  if (v === "map") setTimeout(() => { S.map.invalidateSize(); if (S.boundsStale && S.lastBounds) { S.map.fitBounds(S.lastBounds, { padding: [30, 30] }); S.boundsStale = false; } }, 60);
}
/* fit the map to points – if the map is hidden (mobile), remember and fit when it is shown */
function fitPts(pts) {
  if (pts.length < 2) return;
  S.lastBounds = pts;
  const hidden = isMobile() && document.body.dataset.view !== "map";
  S.boundsStale = hidden;
  if (!hidden) S.map.fitBounds(pts, { padding: [30, 30] });
}
function showMap(fn) { if (isMobile()) setView("map"); setTimeout(fn, isMobile() ? 120 : 0); }

/* =============================== INIT =============================== */
async function init() {
  const [settings, customers, ordersDoc] = await Promise.all([api("/api/settings"), api("/api/customers"), api("/api/orders")]);
  S.settings = settings; S.customers = customers;
  S.orders = ordersDoc.orders || []; S.date = ordersDoc.date || today();
  initMap(); bindTabs(); bindPlan(); bindCustomers(); bindSettings();
  renderAll();
  $("#btn-print").onclick = () => window.print();
  $$(".mnav button").forEach(b => b.onclick = () => setView(b.dataset.view));
  window.addEventListener("resize", () => S.map.invalidateSize());
  if (S.settings._auth_enabled) $("#btn-logout").classList.remove("hidden");
  $("#st-info").textContent = `Data: ${S.settings._storage || "local files"} · cached travel pairs: ${(S.settings._cache || {}).pairs_cached || 0}`;
}

function renderAll() { renderPlanHeader(); renderOrders(); renderCustomers(); renderSettings(); renderChip(); drawPending(); }

function renderChip() {
  const s = S.settings, key = !!(s.google_api_key || "").trim();
  const src = s.traffic_source;
  let txt = "Traffic: OpenStreetMap + Chennai profile";
  if ((src === "auto" && key) || src === "google") txt = key ? "Traffic: Google historic ✓" : "Traffic: Google (no key → OSM fallback)";
  if (src === "haversine") txt = "Traffic: offline estimate";
  $("#traffic-chip").textContent = txt;
}

/* =============================== TABS =============================== */
function bindTabs() {
  $$(".tab").forEach(b => b.onclick = () => {
    $$(".tab").forEach(x => x.classList.toggle("active", x === b));
    $$(".tabpane").forEach(p => p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
  });
}

/* =============================== MAP =============================== */
function initMap() {
  S.map = L.map("map", { zoomControl: true }).setView([13.05, 80.22], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }).addTo(S.map);
  S.map.on("click", e => {
    if (!S.pick) return;
    const { lat, lng } = e.latlng;
    if (S.pick === "customer") { $("#cf-lat").value = lat.toFixed(6); $("#cf-lng").value = lng.toFixed(6); $("#cf-geo-msg").textContent = "Location picked on map."; }
    if (S.pick === "depot") { $("#st-depot-lat").value = lat.toFixed(6); $("#st-depot-lng").value = lng.toFixed(6); }
    S.pick = null; document.body.classList.remove("pick-mode"); toast("Coordinates set"); if (isMobile()) setView("plan");
  });
}
function clearLayers() { S.layers.forEach(l => S.map.removeLayer(l)); S.layers = []; if (S.legend) { S.map.removeControl(S.legend); S.legend = null; } }
function depotMarker(d) {
  return L.marker([d.lat, d.lng], { icon: L.divIcon({ className: "depot-icon", html: "<div>🏬</div>", iconSize: [32, 32], iconAnchor: [16, 16] }), zIndexOffset: 1000 })
    .bindPopup(`<b>${esc(d.name)}</b><br>Depot / loading point`);
}
function stopIcon(label, color) {
  return L.divIcon({ className: "stop-icon" + (color ? "" : " pending"), html: `<div style="background:${color || "#888"}">${label}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });
}
/* before optimisation: grey markers for today's customers */
function drawPending() {
  if (S.result) return;
  clearLayers();
  const d = S.settings.depot; const pts = [];
  if (d && d.lat) { const m = depotMarker(d).addTo(S.map); S.layers.push(m); pts.push([d.lat, d.lng]); }
  S.orders.forEach(o => {
    const c = custById(o.customer_id); if (!c || c.lat == null) return;
    const m = L.marker([c.lat, c.lng], { icon: stopIcon("•") }).bindPopup(`<b>${esc(c.name)}</b><br>${esc(o.qty_kg || "")} kg`).addTo(S.map);
    S.layers.push(m); pts.push([c.lat, c.lng]);
  });
  fitPts(pts);
}
function drawResult() {
  clearLayers();
  const res = S.result; const pts = [];
  const dm = depotMarker(res.depot).addTo(S.map); S.layers.push(dm); pts.push([res.depot.lat, res.depot.lng]);
  const legendRows = [];
  res.routes.filter(r => r.used).forEach(r => {
    r.trips.forEach(t => {
      const line = L.polyline(t.geometry, { color: r.color, weight: 5, opacity: .85, dashArray: t.trip > 1 ? "10 8" : null }).addTo(S.map);
      line.bindTooltip(`${esc(r.vehicle.name)} – trip ${t.trip}: ${t.total_km} km`, { sticky: true });
      S.layers.push(line);
      t.stops.forEach(st => {
        const m = L.marker([st.lat, st.lng], { icon: stopIcon(st.seq, r.color), zIndexOffset: 500 })
          .bindPopup(`<b>${st.seq}. ${esc(st.customer)}</b><br>${esc(r.vehicle.name)} · trip ${t.trip}<br>ETA <b>${st.arrival}</b>${st.window ? " · window " + esc(st.window) : ""}<br>${st.qty_kg} kg · ${st.leg_km} km from previous`)
          .addTo(S.map);
        S.layers.push(m); pts.push([st.lat, st.lng]);
      });
    });
    legendRows.push(`<div><i style="background:${r.color}"></i>${esc(r.vehicle.name)} · ${r.total_km} km${r.trips.length > 1 ? ` · ${r.trips.length} trips` : ""}</div>`);
  });
  res.dropped.forEach(d => { const c = custById(d.customer_id); if (c && c.lat != null) { const m = L.marker([c.lat, c.lng], { icon: stopIcon("!", "#777") }).bindPopup(`<b>${esc(c.name)}</b><br>Not planned: ${esc(d.reason)}`).addTo(S.map); S.layers.push(m); } });
  if (legendRows.length) {
    S.legend = L.control({ position: "bottomleft" });
    S.legend.onAdd = () => { const div = L.DomUtil.create("div", "legend"); div.innerHTML = legendRows.join("") + `<div><i style="background:#999;border-top:2px dashed #555;height:0"></i>dashed = 2nd trip</div>`; return div; };
    S.legend.addTo(S.map);
  }
  fitPts(pts);
}
function focusVehicle(idx) {
  const r = S.result.routes[idx]; if (!r || !r.used) return;
  const pts = r.trips.flatMap(t => t.stops.map(s => [s.lat, s.lng])); pts.push([S.result.depot.lat, S.result.depot.lng]);
  showMap(() => S.map.fitBounds(pts, { padding: [40, 40] }));
}

/* =============================== PLAN TAB =============================== */
function renderPlanHeader() {
  $("#plan-date").value = S.date;
  $("#plan-depart").value = S.settings.departure_time || "05:00";
  $("#plan-strategy").value = S.settings.strategy || "lowest_cost";
  $("#plan-objective").value = S.settings.objective || "time";
}
function customerOptions(selected) {
  return `<option value="">— select customer —</option>` + sortedCustomers().map(c =>
    `<option value="${c.id}" ${c.id === selected ? "selected" : ""}>${esc(c.name)}${c.area ? " – " + esc(c.area) : ""}${c.lat == null ? " ⚠" : ""}</option>`).join("");
}
function renderOrders() {
  const tb = $("#orders-body");
  tb.innerHTML = S.orders.map(o => `<tr data-id="${o.id}" class="${(custById(o.customer_id) || {}).lat == null && o.customer_id ? "row-nogeo" : ""}">
      <td><select data-f="customer_id">${customerOptions(o.customer_id)}</select></td>
      <td data-l="Qty kg"><input type="number" data-f="qty_kg" min="0" step="5" value="${o.qty_kg ?? ""}" placeholder="kg"></td>
      <td data-l="From"><input type="time" data-f="tw_from" value="${o.tw_from || ""}"></td>
      <td data-l="To"><input type="time" data-f="tw_to" value="${o.tw_to || ""}"></td>
      <td data-l="Svc"><input type="number" data-f="service_min" min="0" step="1" value="${o.service_min ?? ""}" placeholder="${S.settings.default_service_min}"></td>
      <td><button class="x" data-del title="Remove">×</button></td></tr>`).join("");
  renderTotals();
}
function renderTotals() {
  const valid = S.orders.filter(o => o.customer_id && +o.qty_kg > 0);
  const kg = valid.reduce((a, o) => a + (+o.qty_kg || 0), 0);
  const fleet = (S.settings.vehicles || []).reduce((a, v) => a + (+v.capacity_kg || 0), 0);
  const trips = S.settings.trips_per_vehicle || 1;
  const el = $("#orders-totals");
  el.classList.toggle("over", kg > fleet * trips);
  $("#orders-count").textContent = valid.length ? `(${valid.length})` : "";
  el.innerHTML = `<b>${valid.length}</b> orders · <b>${kg.toLocaleString("en-IN")} kg</b> · fleet ${S.settings.vehicles.length} × ${S.settings.vehicles.map(v => v.capacity_kg).join("/")} kg = <b>${fleet.toLocaleString("en-IN")} kg</b> per trip`
    + (kg > fleet ? (trips > 1 ? ` · <span style="color:#8a5a00">needs extra trips</span>` : ` · <span style="color:var(--danger)">over capacity – enable extra trips in Settings</span>`) : "");
}
function scheduleSaveOrders() {
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(() => api("/api/orders", "PUT", { date: S.date, orders: S.orders }).catch(() => {}), 500);
}
function addOrder(customer_id, qty, from, to) {
  const c = custById(customer_id);
  S.orders.push({ id: uid(), customer_id: customer_id || "", qty_kg: qty ?? (c ? c.default_qty_kg : "") ?? "",
    tw_from: from ?? (c ? c.default_tw_from : "") ?? "", tw_to: to ?? (c ? c.default_tw_to : "") ?? "", service_min: "", notes: c ? c.notes || "" : "" });
}
function bindPlan() {
  $("#plan-date").onchange = e => { S.date = e.target.value || today(); scheduleSaveOrders(); };
  $("#orders-body").addEventListener("change", e => {
    const tr = e.target.closest("tr"); const o = S.orders.find(x => x.id === tr.dataset.id); if (!o) return;
    const f = e.target.dataset.f; if (!f) return;
    o[f] = e.target.value;
    if (f === "customer_id") {
      const c = custById(o.customer_id);
      if (c) { if (!o.qty_kg) o.qty_kg = c.default_qty_kg || ""; if (!o.tw_from) o.tw_from = c.default_tw_from || ""; if (!o.tw_to) o.tw_to = c.default_tw_to || ""; o.notes = c.notes || ""; }
      renderOrders();
    } else renderTotals();
    scheduleSaveOrders(); S.result = null; drawPending();
  });
  $("#orders-body").addEventListener("click", e => {
    if (!e.target.closest("[data-del]")) return;
    const id = e.target.closest("tr").dataset.id;
    S.orders = S.orders.filter(o => o.id !== id); renderOrders(); scheduleSaveOrders(); S.result = null; drawPending();
  });
  $("#btn-add-order").onclick = () => { addOrder(""); renderOrders(); $("#orders-body tr:last-child select").focus(); };
  $("#btn-add-regulars").onclick = () => {
    let n = 0;
    sortedCustomers().forEach(c => { if (+c.default_qty_kg > 0 && !S.orders.some(o => o.customer_id === c.id)) { addOrder(c.id); n++; } });
    renderOrders(); scheduleSaveOrders(); S.result = null; drawPending(); toast(n ? `Added ${n} regular customers` : "All regulars already added");
  };
  $("#btn-clear-orders").onclick = () => { if (!S.orders.length || confirm("Remove all orders for today?")) { S.orders = []; renderOrders(); scheduleSaveOrders(); S.result = null; drawPending(); } };
  $("#file-import").onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api("/api/import", "POST", fd);
      if (r.customers) S.customers = r.customers;
      S.orders = S.orders.concat(r.orders); renderOrders(); renderCustomers(); scheduleSaveOrders(); S.result = null; drawPending();
      let msg = `Imported ${r.orders.length} orders`; if (r.customers_created) msg += `, added ${r.customers_created} new customers`;
      if (r.problems.length) { msg += `. Problems:\n• ` + r.problems.join("\n• "); alert(msg); } else toast(msg);
    } catch (err) { alert("Import failed: " + err.message); }
    e.target.value = "";
  };
  $("#btn-optimize").onclick = optimize;
}

async function optimize() {
  const orders = S.orders.filter(o => o.customer_id && +o.qty_kg > 0);
  if (!orders.length) return toast("Add at least one order with a customer and quantity");
  const btn = $("#btn-optimize"), st = $("#opt-status");
  btn.disabled = true; st.classList.remove("err");
  const t0 = Date.now();
  const tick = setInterval(() => { st.textContent = `Computing travel times & optimising… ${((Date.now() - t0) / 1000).toFixed(0)} s`; }, 300);
  try {
    const res = await api("/api/optimize", "POST", { orders, settings: {
      date: S.date, departure_time: $("#plan-depart").value || "05:00",
      strategy: $("#plan-strategy").value, objective: $("#plan-objective").value } });
    S.result = res; renderResults(); drawResult();
    if (isMobile()) setView("routes");
    st.textContent = `Done in ${((Date.now() - t0) / 1000).toFixed(1)} s – ${res.summary.total_km} km, ${res.summary.vehicles_used} vehicle(s).`;
    $("#results").scrollTop = 0;
  } catch (err) { st.textContent = "✖ " + err.message; st.classList.add("err"); }
  clearInterval(tick); btn.disabled = false;
}

/* =============================== RESULTS =============================== */
function utilBadge(p) { const cls = p >= 80 ? "ok" : p >= 50 ? "warn" : "bad"; return `<span class="badge ${cls}">${p}% full</span>`; }
function renderResults() {
  const res = S.result, s = res.summary;
  const srcName = { google: "Google historic traffic", osrm: "OpenStreetMap roads + Chennai traffic profile", haversine: "offline estimate" }[s.matrix_source] || s.matrix_source;
  let h = `<div class="summary">
    <div class="kpi"><b>${s.total_km} km</b><span>total distance</span></div>
    <div class="kpi"><b>${Math.floor(s.total_drive_min / 60)}h ${s.total_drive_min % 60}m</b><span>total driving</span></div>
    <div class="kpi"><b>${s.vehicles_used}/${s.vehicles_total}</b><span>vehicles used · ${s.trips} trip${s.trips > 1 ? "s" : ""}</span></div>
    <div class="kpi"><b>${s.utilisation_pct}%</b><span>avg. load per trip · ${s.load_kg.toLocaleString("en-IN")} kg</span></div>
    <div class="kpi good"><b>−${s.saving_km} km</b><span>vs. nearest-first manual plan (${s.saving_pct}%)</span></div>
    <div class="kpi"><b>${s.stops_served}</b><span>stops planned${s.stops_dropped ? ` · <span style="color:var(--danger)">${s.stops_dropped} not planned</span>` : ""}</span></div>
  </div>
  <div class="note">📡 ${esc(s.matrix_note)} · strategy: ${esc(s.strategy.replace("_", " "))} · minimising ${s.objective} · solver ${s.solve_seconds}s</div>`;
  if (res.warnings.length) h += `<div class="warn">⚠ ${res.warnings.map(esc).join("<br>⚠ ")}</div>`;
  h += `<div class="btnrow"><button class="btn" id="btn-excel">⬇ Excel route sheets</button><button class="btn ghost" id="btn-copy-all">📋 Copy all routes (WhatsApp)</button></div>`;

  res.routes.forEach((r, i) => {
    if (!r.used) { h += `<div class="vehicle unused" style="border-left-color:${r.color}"><header><h4>${esc(r.vehicle.name)}</h4><span class="meta">not needed today (${r.capacity_kg} kg free)</span></header></div>`; return; }
    h += `<div class="vehicle" style="border-left-color:${r.color}" data-v="${i}">
      <header>
        <h4>${esc(r.vehicle.name)} <span class="meta">· leave ${r.suggested_departure} · back ${r.return.arrival}</span></h4>
        <span class="meta"><b>${r.total_km} km</b> · ${r.drive_min} min driving · ${r.stops.length} stops · ${r.load_kg.toLocaleString("en-IN")} kg${r.trips.length > 1 ? ` · ${r.trips.length} trips` : ""}${r.wait_min ? ` · ⏳ ${r.wait_min} min waiting` : ""}</span>
        ${utilBadge(r.utilisation_pct)}
        <button class="btn xs ghost" data-focus="${i}">🗺 Focus</button>
        <a class="btn xs" href="${r.trips[0].gmaps_urls[0]}" target="_blank" rel="noopener">▶ Google Maps</a>
        <button class="btn xs ghost" data-copy="${i}">📋 WhatsApp</button>
      </header><div class="body">`;
    r.trips.forEach(t => {
      if (r.trips.length > 1) h += `<div class="trip-title">Trip ${t.trip} <span>· leave depot ${t.depart} · ${t.load_kg} kg ${utilBadge(t.utilisation_pct)} · ${t.total_km} km · back ${t.return.arrival}${t.reload_wait_min ? ` · waits ${t.reload_wait_min} min at depot` : ""}</span>${t.trip > 1 ? ` <a class="btn xs ghost" href="${t.gmaps_urls[0]}" target="_blank" rel="noopener">▶ Maps trip ${t.trip}</a>` : ""}</div>`;
      h += `<table class="stops"><thead><tr><th>#</th><th>Customer</th><th>ETA</th><th>Window</th><th class="r">Qty</th><th class="r col-opt">Left on truck</th><th class="r col-opt">Leg</th><th class="col-opt">Notes</th></tr></thead><tbody>`;
      t.stops.forEach(st => {
        const phone = st.phone ? `<a href="tel:${esc(st.phone.replace(/\s+/g, ""))}" style="text-decoration:none">📞 ${esc(st.phone)}</a>` : "";
        h += `<tr><td class="num" style="color:${r.color}">${st.seq}</td>
          <td><b>${esc(st.customer)}</b>${st.area ? ` <span class="muted">· ${esc(st.area)}</span>` : ""}${phone ? `<br><span class="muted small">${phone}</span>` : ""}
              <span class="mob muted small"><br>${st.leg_km} km · ${st.leg_min} min · ${st.remaining_kg} kg left${st.notes ? ` · ${esc(st.notes)}` : ""}</span></td>
          <td><b>${st.arrival}</b>${st.wait_min ? `<br><span class="badge warn">wait ${st.wait_min}m</span>` : ""}</td>
          <td>${st.window ? esc(st.window) : '<span class="muted">any</span>'}</td>
          <td class="r">${st.qty_kg} kg</td><td class="r col-opt">${st.remaining_kg} kg</td>
          <td class="r col-opt">${st.leg_km} km<br><span class="muted small">${st.leg_min} min</span></td>
          <td class="small col-opt">${esc(st.notes || "")}</td></tr>`;
      });
      h += `<tr class="ret"><td></td><td>Return to depot <span class="mob muted small">· ${t.return.leg_km} km · ${t.return.leg_min} min</span></td><td>${t.return.arrival}</td><td></td><td></td><td class="col-opt"></td><td class="r col-opt">${t.return.leg_km} km<br><span class="muted small">${t.return.leg_min} min</span></td><td class="col-opt"></td></tr></tbody></table>`;
    });
    h += `</div></div>`;
  });
  if (res.dropped.length) {
    h += `<div class="dropped"><b>⚠ Not planned (${res.dropped.length})</b><ul style="margin:6px 0 0 18px;padding:0">` +
      res.dropped.map(d => `<li><b>${esc(d.customer)}</b> – ${d.qty_kg ?? ""} kg${d.window ? ` (window ${esc(d.window)})` : ""}: ${esc(d.reason)}</li>`).join("") + `</ul></div>`;
  }
  const el = $("#results"); el.innerHTML = h;
  $("#btn-excel").onclick = exportExcel;
  $("#btn-copy-all").onclick = () => copyText(res.routes.filter(r => r.used).map(r => r.text).join("\n\n"));
  $$("[data-copy]", el).forEach(b => b.onclick = e => { e.stopPropagation(); copyText(res.routes[+b.dataset.copy].text); });
  $$("[data-focus]", el).forEach(b => b.onclick = e => { e.stopPropagation(); focusVehicle(+b.dataset.focus); });
  $$(".vehicle header", el).forEach(hd => hd.onclick = e => { if (e.target.closest("a,button")) return; hd.parentElement.classList.toggle("collapsed"); });
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied – paste into WhatsApp"); }
  catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Copied"); }
}
async function exportExcel() {
  try {
    const blob = await api("/api/export.xlsx", "POST", S.result);
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `onion_routes_${S.result.summary.date}.xlsx`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  } catch (err) { alert("Export failed: " + err.message); }
}

/* =============================== CUSTOMERS TAB =============================== */
function renderCustomers() {
  const q = ($("#cust-search").value || "").toLowerCase();
  const rows = sortedCustomers().filter(c => !q || `${c.name} ${c.area} ${c.address}`.toLowerCase().includes(q));
  $("#cust-body").innerHTML = rows.map(c => `<tr class="${c.lat == null ? "row-nogeo" : ""}" data-id="${c.id}">
    <td><b>${esc(c.name)}</b>${c.phone ? `<br><span class="muted small">${esc(c.phone)}</span>` : ""}</td>
    <td>${esc(c.area || "")}</td>
    <td>${c.default_qty_kg ? c.default_qty_kg + " kg" : '<span class="muted">–</span>'}</td>
    <td class="small">${c.default_tw_from || c.default_tw_to ? esc((c.default_tw_from || "…") + "–" + (c.default_tw_to || "…")) : '<span class="muted">any</span>'}</td>
    <td style="white-space:nowrap"><button class="btn xs ghost" data-locate title="Show on map">📍</button> <button class="btn xs ghost" data-edit>✎</button> <button class="x" data-del title="Delete">×</button></td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">No customers yet – add one or import an Excel file.</td></tr>`;
  const missing = S.customers.filter(c => c.lat == null).length;
  $("#btn-geocode-missing").textContent = missing ? `🔍 Geocode ${missing} customer(s) without location` : "🔍 All customers have coordinates ✓";
  $("#btn-geocode-missing").disabled = !missing;
}
function showCustForm(c) {
  const f = $("#cust-form"); f.classList.remove("hidden");
  $("#cf-id").value = c?.id || ""; $("#cf-name").value = c?.name || ""; $("#cf-area").value = c?.area || "";
  $("#cf-address").value = c?.address || ""; $("#cf-lat").value = c?.lat ?? ""; $("#cf-lng").value = c?.lng ?? "";
  $("#cf-phone").value = c?.phone || ""; $("#cf-qty").value = c?.default_qty_kg ?? ""; $("#cf-notes").value = c?.notes || "";
  $("#cf-from").value = c?.default_tw_from || ""; $("#cf-to").value = c?.default_tw_to || ""; $("#cf-geo-msg").textContent = "";
  $("#cf-name").focus();
  if (c && c.lat != null) S.map.setView([c.lat, c.lng], 15);
}
async function saveCustomers() { S.customers = await api("/api/customers", "PUT", S.customers); renderCustomers(); renderOrders(); }
function bindCustomers() {
  $("#cust-search").oninput = renderCustomers;
  $("#btn-new-cust").onclick = () => showCustForm(null);
  $("#btn-cust-cancel").onclick = () => $("#cust-form").classList.add("hidden");
  $("#btn-pick").onclick = () => { S.pick = "customer"; document.body.classList.add("pick-mode"); showMap(() => {}); toast("Tap the exact location on the map", 4000); };
  $("#btn-geocode").onclick = async () => {
    const addr = $("#cf-address").value.trim(); if (!addr) return toast("Type an address or paste a Google Maps link first");
    $("#cf-geo-msg").textContent = "Searching…";
    try {
      const g = await api("/api/geocode", "POST", { address: addr });
      $("#cf-lat").value = g.lat.toFixed(6); $("#cf-lng").value = g.lng.toFixed(6);
      $("#cf-geo-msg").textContent = `✓ ${g.display} (${g.source})`;
      S.map.setView([g.lat, g.lng], 15);
      const m = L.marker([g.lat, g.lng]).addTo(S.map); S.layers.push(m); setTimeout(() => S.map.removeLayer(m), 6000);
    } catch (err) { $("#cf-geo-msg").textContent = "✖ " + err.message; }
  };
  $("#cust-form").onsubmit = async e => {
    e.preventDefault();
    const id = $("#cf-id").value || uid();
    const c = { id, name: $("#cf-name").value.trim(), area: $("#cf-area").value.trim(), address: $("#cf-address").value.trim(),
      lat: num($("#cf-lat").value), lng: num($("#cf-lng").value), phone: $("#cf-phone").value.trim(),
      default_qty_kg: num($("#cf-qty").value), default_tw_from: $("#cf-from").value, default_tw_to: $("#cf-to").value, notes: $("#cf-notes").value.trim() };
    if (!c.name) return;
    const i = S.customers.findIndex(x => x.id === id); if (i >= 0) S.customers[i] = c; else S.customers.push(c);
    await saveCustomers(); $("#cust-form").classList.add("hidden"); toast("Customer saved"); drawPending();
  };
  $("#cust-body").addEventListener("click", async e => {
    const tr = e.target.closest("tr"); if (!tr) return; const c = custById(tr.dataset.id); if (!c) return;
    if (e.target.closest("[data-edit]")) showCustForm(c);
    else if (e.target.closest("[data-locate]")) { if (c.lat != null) showMap(() => { S.map.setView([c.lat, c.lng], 15); L.popup().setLatLng([c.lat, c.lng]).setContent(`<b>${esc(c.name)}</b>`).openOn(S.map); }); else toast("No coordinates yet – edit and press Find"); }
    else if (e.target.closest("[data-del]")) { if (confirm(`Delete ${c.name}?`)) { S.customers = S.customers.filter(x => x.id !== c.id); S.orders = S.orders.filter(o => o.customer_id !== c.id); await saveCustomers(); scheduleSaveOrders(); } }
  });
  $("#btn-geocode-missing").onclick = async () => {
    const missing = S.customers.filter(c => c.lat == null && c.address);
    let ok = 0;
    for (const c of missing) {
      try { const g = await api("/api/geocode", "POST", { address: c.address }); c.lat = g.lat; c.lng = g.lng; ok++; renderCustomers(); } catch {}
    }
    await saveCustomers(); toast(`Geocoded ${ok} of ${missing.length}`); drawPending();
  };
}

/* =============================== SETTINGS TAB =============================== */
function renderSettings() {
  const s = S.settings;
  $("#st-depot-name").value = s.depot.name || ""; $("#st-depot-address").value = s.depot.address || "";
  $("#st-depot-lat").value = s.depot.lat ?? ""; $("#st-depot-lng").value = s.depot.lng ?? "";
  renderVehicles();
  $("#st-depart").value = s.departure_time; $("#st-hours").value = s.max_route_hours; $("#st-service").value = s.default_service_min;
  $("#st-trips").value = s.trips_per_vehicle ?? 2; $("#st-reload").value = s.reload_min ?? 20; $("#st-solver").value = s.solver_seconds;
  $("#st-source").value = s.traffic_source; $("#st-gkey").value = s.google_api_key || ""; $("#st-gmodel").value = s.traffic_model || "best_guess";
  $("#st-goffset").value = s.traffic_sample_offset_min ?? 45; $("#st-osrmf").value = s.osrm_base_factor ?? 1.1;
  renderProfile(s.traffic_profile);
}
function renderVehicles() {
  $("#veh-body").innerHTML = (S.settings.vehicles || []).map((v, i) => `<tr data-i="${i}">
    <td><input data-vf="name" value="${esc(v.name)}"></td><td><input data-vf="capacity_kg" type="number" min="50" step="50" value="${v.capacity_kg}"></td>
    <td><button class="x" data-vdel title="Remove">×</button></td></tr>`).join("");
}
function renderProfile(p) {
  $("#profile-grid").innerHTML = p.map((v, h) => `<label>${String(h).padStart(2, "0")}:00<input type="number" step="0.05" min="0.8" max="3" value="${v}" data-h="${h}"></label>`).join("");
}
function bindSettings() {
  $("#veh-body").addEventListener("input", e => { const i = +e.target.closest("tr").dataset.i, f = e.target.dataset.vf; if (f) S.settings.vehicles[i][f] = f === "capacity_kg" ? +e.target.value : e.target.value; });
  $("#veh-body").addEventListener("click", e => { if (e.target.closest("[data-vdel]")) { S.settings.vehicles.splice(+e.target.closest("tr").dataset.i, 1); renderVehicles(); } });
  $("#btn-add-veh").onclick = () => { const n = S.settings.vehicles.length + 1; S.settings.vehicles.push({ id: "V" + n, name: `Vehicle ${n}`, capacity_kg: S.settings.vehicles[0]?.capacity_kg || 750 }); renderVehicles(); };
  $("#btn-profile-reset").onclick = () => renderProfile([1, 1, 1, 1, 1, 1.05, 1.15, 1.35, 1.6, 1.8, 1.7, 1.55, 1.45, 1.4, 1.4, 1.5, 1.65, 1.85, 1.95, 1.9, 1.65, 1.35, 1.15, 1.05]);
  $("#btn-depot-pick").onclick = () => { S.pick = "depot"; document.body.classList.add("pick-mode"); showMap(() => {}); toast("Tap the depot location on the map", 4000); };
  $("#btn-depot-geocode").onclick = async () => {
    try { const g = await api("/api/geocode", "POST", { address: $("#st-depot-address").value }); $("#st-depot-lat").value = g.lat.toFixed(6); $("#st-depot-lng").value = g.lng.toFixed(6); S.map.setView([g.lat, g.lng], 15); toast("Found: " + g.display, 4000); }
    catch (err) { alert(err.message); }
  };
  $("#btn-save-settings").onclick = async () => {
    const s = S.settings;
    s.depot = { name: $("#st-depot-name").value.trim() || "Depot", address: $("#st-depot-address").value.trim(), lat: num($("#st-depot-lat").value), lng: num($("#st-depot-lng").value) };
    if (s.depot.lat == null || s.depot.lng == null) return alert("Depot needs coordinates – use 🔍 or Pick on map.");
    s.departure_time = $("#st-depart").value || "05:00"; s.max_route_hours = +$("#st-hours").value || 6; s.default_service_min = +$("#st-service").value || 0;
    s.trips_per_vehicle = +$("#st-trips").value || 1; s.reload_min = +$("#st-reload").value || 0; s.solver_seconds = +$("#st-solver").value || 8;
    s.traffic_source = $("#st-source").value; s.google_api_key = $("#st-gkey").value.trim(); s.traffic_model = $("#st-gmodel").value;
    s.traffic_sample_offset_min = +$("#st-goffset").value || 0; s.osrm_base_factor = +$("#st-osrmf").value || 1.1;
    s.traffic_profile = $$("#profile-grid input").map(i => +i.value || 1);
    s.vehicles = s.vehicles.filter(v => +v.capacity_kg > 0);
    try { S.settings = await api("/api/settings", "PUT", s); renderSettings(); renderTotals(); renderPlanHeader(); renderChip(); $("#settings-msg").textContent = "Saved ✓"; setTimeout(() => $("#settings-msg").textContent = "", 2500); drawPending(); }
    catch (err) { alert("Could not save: " + err.message); }
  };
}

init().catch(err => { console.error(err); alert("Failed to start: " + err.message); });
