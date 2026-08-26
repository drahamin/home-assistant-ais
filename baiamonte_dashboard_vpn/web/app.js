const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function render(status) {
  $('state').textContent = status.state.toUpperCase();
  $('state').className = `state ${status.state}`;
  $('profile').textContent = status.profile_installed ? 'Installed securely' : 'Not installed';
  $('tunnel').textContent = status.connected ? 'Connected' : (status.process_running ? 'Connecting…' : 'Offline');
  $('message').textContent = status.last_error || status.last_log || '—';
  $('dashboard-url').textContent = status.dashboard_url || '—';
  $('connection-title').textContent = status.connected ? 'Cloud tunnel online' : (status.profile_installed ? 'Connector is starting' : 'Waiting for setup');
  $('reconnect').disabled = !status.profile_installed;
}

async function refresh() {
  try { render(await api('api/status')); } catch (error) { $('message').textContent = error.message; }
}

$('save').addEventListener('click', async () => {
  const profile = $('profile-input').value;
  $('form-message').textContent = 'Validating…';
  try {
    await api('api/profile', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({profile})});
    $('profile-input').value = '';
    $('form-message').textContent = 'Profile saved. Connecting…';
    await refresh();
  } catch (error) { $('form-message').textContent = error.message; }
});

$('install-token').addEventListener('click', async () => {
  const token = $('token-input').value;
  $('form-message').textContent = 'Downloading the protected profile…';
  try {
    await api('api/setup-token', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token})});
    $('token-input').value = '';
    $('form-message').textContent = 'Profile installed. Connecting…';
    await refresh();
  } catch (error) { $('form-message').textContent = error.message; }
});

$('reconnect').addEventListener('click', async () => {
  await api('api/reconnect', {method: 'POST'});
  await refresh();
});

$('copy').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('dashboard-url').textContent);
  $('copy').textContent = 'Copied';
  setTimeout(() => $('copy').textContent = 'Copy', 1200);
});

refresh();
setInterval(refresh, 5000);
