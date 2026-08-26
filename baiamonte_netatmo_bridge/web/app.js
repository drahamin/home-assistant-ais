const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function when(value) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function endpoint(label, entity, online) {
  return `<div class="endpoint ${online ? "" : "off"}"><small>${label} · ${online ? "AVAILABLE" : "UNAVAILABLE"}</small><code>${esc(entity || "Not configured")}</code></div>`;
}

function render(data) {
  const counts = data.counts || {};
  $("health").textContent = data.health === "local" ? "Local first" : data.health === "cloud" ? "Cloud only" : data.health || "Unknown";
  $("local-count").textContent = counts.local ?? 0;
  $("cloud-count").textContent = counts.cloud ?? 0;
  $("paired-count").textContent = counts.paired ?? 0;
  $("updated").textContent = `Updated ${when(data.last_refresh)}`;
  const routes = data.routes || [];
  $("notice").className = `notice ${data.last_error ? "error" : ""}`;
  $("notice").textContent = data.last_error || (!routes.length ? "No matching entities yet. Pair the gateway with HomeKit Device/Matter, keep Netatmo enabled, or add manual pairs in the app configuration." : "");
  $("devices").innerHTML = routes.map(route => `<div class="device"><div class="device-name"><b>${esc(route.name)}</b><span>${esc(route.state)}</span></div><span class="route ${esc(route.active_path)}">${esc(route.active_path)}</span>${endpoint("LOCAL", route.local_entity, route.local_available)}${endpoint("CLOUD", route.cloud_entity, route.cloud_available)}</div>`).join("");
  $("events").innerHTML = (data.events || []).map(event => `<div class="event"><time>${esc(when(event.at))}</time><i class="${esc(event.level)}">${esc(event.level)}</i><span>${esc(event.message)}</span></div>`).join("") || '<div class="notice">No activity recorded yet.</div>';
}

async function refresh(requestRefresh = false) {
  try {
    if (requestRefresh) await fetch("api/refresh", {cache: "no-store"});
    const response = await fetch("api/status", {cache: "no-store"});
    render(await response.json());
  } catch (error) {
    $("notice").className = "notice error";
    $("notice").textContent = `Dashboard connection failed: ${error.message}`;
  }
}

$("refresh").addEventListener("click", () => refresh(true));
refresh();
setInterval(refresh, 5000);
