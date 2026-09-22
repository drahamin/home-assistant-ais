const $ = id => document.getElementById(id);
const titles = {overview:'Power overview',trends:'Energy trends',configuration:'Configuration'};
let latestHistory = [];
let configLoaded = false;

async function api(path, options={}) {
  const response = await fetch(`./api/${path}`, {cache:'no-store', ...options});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}

function fmt(value, unit='', digits=0) {
  return value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value).toFixed(digits)}${unit}`;
}
function online(value) { return value === true ? 'Online' : value === false ? 'Offline' : 'Unknown'; }
function shortEntity(value) { return String(value).replace(/^switch\./,'').replaceAll('_',' '); }

function showPage(page) {
  document.querySelectorAll('.page').forEach(node => node.classList.toggle('active', node.id === page));
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === page));
  $('page-title').textContent = titles[page];
  if (page === 'trends') refreshHistory();
  if (page === 'configuration' && !configLoaded) loadConfig();
}
document.querySelectorAll('.nav').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.go)));

function renderCategories(categories={}) {
  const rows = [
    ['first','Shed first'],['stage_2','Flexible'],['stage_3','Protected'],['emergency','Emergency']
  ];
  $('category-list').innerHTML = rows.map(([key,label],index) => {
    const names = (categories[key] || []).map(shortEntity).join(', ') || 'No switches assigned';
    return `<div class="category-row"><i>${index+1}</i><b>${label}</b><span>${names}</span></div>`;
  }).join('');
}

async function refreshStatus() {
  try {
    const s = await api('status');
    const soc = Number(s.soc);
    const healthy = s.service === 'running' && s.telemetry_ok;
    const automatic = s.control_mode === 'automatic';
    $('mode').textContent = `${automatic ? 'Automatic' : 'Observe'} · ${s.mode || 'unknown'}`;
    $('mode').classList.toggle('automatic', automatic);
    $('side-mode').textContent = `${automatic ? 'Automatic' : 'Observe'} · ${s.mode || 'unknown'}`;
    $('side-status').textContent = healthy ? 'Live telemetry healthy' : 'Telemetry needs attention';
    ['side-light','hero-light'].forEach(id => {$(id).className = healthy ? 'online' : 'error';});
    $('hero-status').textContent = healthy ? s.reason : (s.last_error || 'Battery telemetry unavailable');
    $('last-check').textContent = s.last_evaluation ? `Evaluated ${new Date(s.last_evaluation*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}` : 'Waiting for evaluation';
    $('health-badge').textContent = healthy ? 'Healthy' : 'Attention';
    $('health-badge').className = `status-badge ${healthy ? 'healthy' : 'error'}`;
    $('soc').textContent = Number.isFinite(soc) ? `${soc.toFixed(0)}%` : '—';
    const ring = $('soc-ring');
    ring.style.strokeDashoffset = Number.isFinite(soc) ? String(439.823 * (1 - Math.max(0,Math.min(100,soc))/100)) : '439.823';
    ring.style.stroke = soc <= 35 ? '#c94f57' : soc <= 60 ? '#c48622' : '#d4af37';
    $('runtime').textContent = fmt(s.runtime_hours,' h',1);
    $('forecast-load').textContent = `Planning load ${fmt(s.expected_load_w,' W',0)}`;
    $('battery-power').textContent = fmt(Math.abs(s.battery_power_w),' W',0);
    $('power-direction').textContent = s.battery_power_w == null ? 'Waiting for telemetry' : s.battery_power_w < 0 ? 'Charging battery bank' : 'Discharging battery bank';
    $('load').textContent = fmt(s.estate_load_w,' W',0);
    $('learning').textContent = `${fmt(s.learned_load_w,' W',0)} learned · ${s.samples || 0} samples`;
    $('solar').textContent = fmt(s.solar_power_w,' W',0);
    $('solar-forecast').textContent = `${fmt(s.solar_remaining_today_kwh,' kWh',1)} remaining · ${fmt(s.solar_tomorrow_kwh,' kWh',1)} tomorrow`;
    $('weather').textContent = s.weather_condition ? String(s.weather_condition).replaceAll('-',' ') : 'Unknown';
    $('renewable-outlook').textContent = `${s.renewable_outlook || 'unavailable'} outlook · ${s.season || 'unknown'} · ${fmt(s.solar_credit_kwh,' kWh',1)} credited`;
    $('internet').textContent = online(s.internet_online);
    $('cameras').textContent = online(s.cameras_powered);
    $('decision-mode').textContent = String(s.mode || 'waiting').replaceAll('_',' ');
    $('reason').textContent = s.reason || 'Waiting for telemetry.';
    $('managed').textContent = (s.managed_off || []).length ? `${s.managed_off.length} load(s) managed off: ${(s.managed_off || []).map(shortEntity).join(', ')}` : 'No loads managed off';
    $('error').textContent = s.last_error || '';
    renderCategories(s.shed_categories);
    const banner = document.querySelector('.safety-banner');
    banner.classList.toggle('automatic', automatic);
    $('safety-title').textContent = automatic ? 'Automatic mode — categorized loads may switch' : 'Observe mode — no physical switching';
    $('safety-copy').textContent = automatic ? 'Power Guard can shed and later restore only the validated category members shown below.' : 'Recommendations are visible, but no load will be operated until Automatic is explicitly enabled.';
  } catch (error) {
    $('error').textContent = error.message;
    ['side-light','hero-light'].forEach(id => $(id).className = 'error');
  }
}

function canvasSize(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(260, canvas.clientWidth);
  const height = Math.max(180, canvas.clientHeight);
  canvas.width = width * ratio; canvas.height = height * ratio;
  const context = canvas.getContext('2d'); context.scale(ratio,ratio);
  return {context,width,height};
}

function drawChart(id, rows, series, fixedMin=null, fixedMax=null) {
  const canvas = $(id), {context:ctx,width,height} = canvasSize(canvas);
  ctx.clearRect(0,0,width,height);
  const pad = {left:48,right:15,top:12,bottom:28};
  const values = rows.flatMap(row => series.map(item => Number(row[item.key])).filter(Number.isFinite));
  if (rows.length < 2 || !values.length) return;
  let min = fixedMin == null ? Math.min(...values,0) : fixedMin;
  let max = fixedMax == null ? Math.max(...values,1) : fixedMax;
  if (max === min) max = min + 1;
  const x = i => pad.left + i/(rows.length-1)*(width-pad.left-pad.right);
  const y = value => pad.top + (max-value)/(max-min)*(height-pad.top-pad.bottom);
  ctx.font='10px system-ui';ctx.fillStyle='#8c8172';ctx.strokeStyle='rgba(140,129,114,.2)';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){const value=max-(max-min)*i/4,yy=y(value);ctx.beginPath();ctx.moveTo(pad.left,yy);ctx.lineTo(width-pad.right,yy);ctx.stroke();ctx.fillText(Math.round(value).toLocaleString(),4,yy+3)}
  const first=new Date(rows[0].timestamp*1000),last=new Date(rows.at(-1).timestamp*1000);ctx.fillText(first.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),pad.left,height-7);const end=last.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});ctx.fillText(end,width-pad.right-ctx.measureText(end).width,height-7);
  series.forEach(item=>{ctx.strokeStyle=item.color;ctx.lineWidth=2;ctx.lineJoin='round';ctx.beginPath();let drawing=false;rows.forEach((row,index)=>{const value=Number(row[item.key]);if(!Number.isFinite(value)){drawing=false;return}if(!drawing){ctx.moveTo(x(index),y(value));drawing=true}else ctx.lineTo(x(index),y(value))});ctx.stroke()});
}

function renderHistory() {
  const rows = latestHistory;
  $('power-empty').style.display = rows.length < 2 ? 'grid' : 'none';
  drawChart('power-chart',rows,[{key:'battery_power_w',color:'#d4af37'},{key:'estate_load_w',color:'#8b4513'},{key:'solar_power_w',color:'#6f8252'}]);
  drawChart('soc-chart',rows,[{key:'soc',color:'#d4af37'}],0,100);
  drawChart('runtime-chart',rows,[{key:'runtime_hours',color:'#6f8252'}],0,null);
}
async function refreshHistory(){try{const data=await api('history');latestHistory=data.history||[];renderHistory()}catch(error){$('power-empty').textContent=error.message}}
window.addEventListener('resize',()=>{if(document.querySelector('#trends.active'))renderHistory()});

const numericNames = new Set(['battery_capacity_kwh','reserve_soc','economy_soc','conserve_soc','protect_soc','emergency_soc','restore_soc','economy_runtime_hours','conserve_runtime_hours','protect_runtime_hours','emergency_runtime_hours','forecast_margin_percent','minimum_planning_load_w','restore_charge_power_w','restore_solar_surplus_w','solar_forecast_credit_percent','maximum_solar_credit_kwh','overnight_buffer_hours','poor_weather_load_penalty_percent','evaluation_interval_seconds','confirm_seconds','restore_stable_seconds','history_hours','learning_alpha']);
async function loadConfig(){
  try{const data=await api('config');Object.entries(data.options||{}).forEach(([name,value])=>{const field=document.querySelector(`[name="${name}"]`);if(field&&value!=null)field.value=value});$('config-source').textContent=`Source: ${data.source}`;$('history-window').textContent=`${data.options.history_hours||24} hours`;configLoaded=true}catch(error){$('save-title').textContent='Could not load configuration';$('save-message').textContent=error.message}
}
$('config-form').addEventListener('input',()=>{$('save-title').textContent='Unsaved changes';$('save-message').textContent='Save to validate and apply these settings.'});
$('config-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('save-button');button.disabled=true;$('save-title').textContent='Validating configuration…';
  const payload={};new FormData(event.currentTarget).forEach((value,key)=>{payload[key]=numericNames.has(key)?Number(value):value});
  try{const data=await api('config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});$('save-title').textContent='Configuration saved';$('save-message').textContent=data.message;event.currentTarget.elements.automatic_confirmation.value='';configLoaded=false;await loadConfig();await refreshStatus()}catch(error){$('save-title').textContent='Configuration rejected';$('save-message').textContent=error.message}finally{button.disabled=false}
});

refreshStatus();refreshHistory();setInterval(refreshStatus,5000);setInterval(refreshHistory,60000);
