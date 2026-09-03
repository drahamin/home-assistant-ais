const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
let snapshot = {routes: [], counts: {}};
let activeFilter = "all";

function when(value) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function routeLabel(route) {
  if (route.active_path === "local") return (route.local_platform || "local").replaceAll("_", " ");
  return route.active_path;
}

function controlButtons(route, enabled) {
  if (!enabled || route.active_path === "offline") return "";
  const services = new Set(route.controls || []);
  const choices = [];
  const add = (service, label, tone = "") => {
    if (services.has(service)) choices.push(`<button class="control ${tone}" data-target="${esc(route.local_entity || route.cloud_entity)}" data-service="${esc(service)}" type="button">${esc(label)}</button>`);
  };
  add("turn_on", "On", "primary");
  add("turn_off", "Off");
  add("open_cover", "Open", "primary");
  add("close_cover", "Close");
  add("stop_cover", "Stop");
  add("open_valve", "Open", "primary");
  add("close_valve", "Close");
  add("stop_valve", "Stop");
  add("start", "Start", "primary");
  add("pause", "Pause");
  add("stop", "Stop");
  add("return_to_base", "Dock");
  return choices.length ? `<div class="controls">${choices.join("")}</div>` : "";
}

function endpoint(label, entity, platform, online) {
  const status = !entity ? "NOT CONFIGURED" : online ? "AVAILABLE" : "UNAVAILABLE";
  return `<div class="endpoint ${online ? "" : "off"}"><small>${esc(label)} · ${status}</small><code title="${esc(entity || "No counterpart")}">${esc(entity || "No counterpart")}</code><span>${esc(platform || "—")}</span></div>`;
}

function renderRoutes() {
  const routes = (snapshot.routes || []).filter(route => activeFilter === "all" || route.active_path === activeFilter);
  $("devices").innerHTML = routes.map(route => `<article class="device">
    <div class="device-top"><div class="device-name"><b>${esc(route.name)}</b><span>${esc(route.domain)} · state: ${esc(route.state)}</span></div><span class="route ${esc(route.active_path)}">${esc(routeLabel(route))}</span></div>
    <div class="endpoints">${endpoint("LOCAL", route.local_entity, route.local_platform, route.local_available)}${endpoint("CLOUD", route.cloud_entity, route.cloud_platform, route.cloud_available)}</div>
    ${controlButtons(route, snapshot.controls_enabled)}
  </article>`).join("") || '<div class="empty">No devices match this view.</div>';
}

function render(data) {
  snapshot = data;
  const counts = data.counts || {};
  $("health").textContent = data.health === "local" ? "Local first" : data.health === "cloud" ? "Cloud only" : data.health || "Unknown";
  $("local-count").textContent = counts.local ?? 0;
  $("cloud-count").textContent = counts.cloud ?? 0;
  $("offline-count").textContent = counts.offline ?? 0;
  $("updated").textContent = `Updated ${when(data.last_refresh)}`;
  $("notice").className = `notice ${data.last_error ? "error" : ""}`;
  $("notice").textContent = data.last_error || (!(data.routes || []).length ? "No Tuya routes found. Configure Tuya Local/LocalTuya, enroll Matter or ZHA entities, or add manual route pairs in the app configuration." : data.controls_enabled ? "" : "Dashboard controls are disabled; this is a read-only route view.");
  const cloudOnly = (data.routes || []).filter(route => route.active_path === "cloud").length;
  const offline = counts.offline || 0;
  $("readiness-title").textContent = offline ? `${offline} device${offline === 1 ? "" : "s"} offline` : cloudOnly ? `${cloudOnly} cloud-only route${cloudOnly === 1 ? "" : "s"}` : (counts.total ? "Ready for an outage test" : "Add pilot devices");
  $("readiness-copy").textContent = offline ? "Restore these routes before testing the WAN disconnect." : cloudOnly ? "These devices will stop working when the WAN is disconnected. Migrate or replace them before relying on local operation." : "Disconnect only the WAN—not the LAN—and verify commands, physical state changes, device restarts, and a Home Assistant restart.";
  renderRoutes();
  $("events").innerHTML = (data.events || []).map(event => `<div class="event"><time>${esc(when(event.at))}</time><i class="${esc(event.level)}">${esc(event.level)}</i><span>${esc(event.message)}</span></div>`).join("") || '<div class="empty">No activity recorded yet.</div>';
}

async function apiPost(path, payload = {}) {
  const response = await fetch(path, {method: "POST", cache: "no-store", headers: {"Content-Type": "application/json", "X-Baiamonte-Request": "1"}, body: JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}

async function refresh(requestRefresh = false) {
  try {
    if (requestRefresh) await apiPost("api/refresh");
    const response = await fetch("api/status", {cache: "no-store"});
    if (!response.ok) throw new Error(`Status failed (${response.status})`);
    render(await response.json());
  } catch (error) {
    $("notice").className = "notice error";
    $("notice").textContent = `Dashboard connection failed: ${error.message}`;
  }
}

$("refresh").addEventListener("click", () => refresh(true));
document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
  activeFilter = button.dataset.filter;
  document.querySelectorAll(".filter").forEach(item => item.classList.toggle("active", item === button));
  renderRoutes();
}));
$("devices").addEventListener("click", async event => {
  const button = event.target.closest("button.control");
  if (!button) return;
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = "Sending…";
  try {
    const result = await apiPost("api/control", {target: button.dataset.target, service: button.dataset.service, data: {}});
    button.textContent = result.path === "local" ? "Sent locally" : "Sent by cloud";
    window.setTimeout(() => refresh(), 700);
  } catch (error) {
    button.textContent = "Failed";
    $("notice").className = "notice error";
    $("notice").textContent = error.message;
  } finally {
    window.setTimeout(() => { button.disabled = false; button.textContent = previous; }, 1600);
  }
});

refresh();
window.setInterval(refresh, 5000);
