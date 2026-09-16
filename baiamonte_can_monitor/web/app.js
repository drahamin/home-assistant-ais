const pages = [...document.querySelectorAll('.page')];
const navButtons = [...document.querySelectorAll('.nav')];
const titles = {overview:'Overview',battery:'Battery data',trends:'Battery trends',recovery:'Recovery control',traffic:'Bus traffic',diagnostics:'Diagnostics',wiring:'Wiring help'};
const $ = id => document.getElementById(id);
let latest = null;
let refreshTimer = null;
let trendsLoadedAt = 0;
const renderedHtml = new Map();

function replaceHtml(id, html){
  if(renderedHtml.get(id)===html)return;
  $(id).innerHTML=html;
  renderedHtml.set(id,html);
}

function showPage(id){
  pages.forEach(page=>page.classList.toggle('active',page.id===id));
  navButtons.forEach(button=>button.classList.toggle('active',button.dataset.page===id));
  $('page-title').textContent=titles[id]||'CAN Monitor';
  navButtons.find(button=>button.dataset.page===id)?.scrollIntoView({behavior:'smooth',block:'nearest',inline:'center'});
  window.scrollTo({top:0,behavior:'smooth'});
  if(id==='trends'&&Date.now()-trendsLoadedAt>300000)refreshTrends();
}
navButtons.forEach(button=>button.addEventListener('click',()=>showPage(button.dataset.page)));
document.querySelectorAll('[data-go]').forEach(button=>button.addEventListener('click',()=>showPage(button.dataset.go)));

const chartColors=['#d4af37','#6f8252','#c48622','#7d68a7','#4f8f9d','#c2645a','#5974a8','#9a704e','#799958','#b65e88','#3c8c78','#8f6db2','#aa7d28','#547b9e','#bb6551','#657247'];
function formatChartValue(value,unit){
  const digits=unit==='V'?3:unit==='kWh'?2:unit==='%'?0:0;
  return `${Number(value).toFixed(digits)} ${unit}`;
}
function lineChart(hostId,payload){
  const host=$(hostId), series=payload.series.filter(item=>item.points.length);
  if(!series.length){host.innerHTML='<div class="chart-empty">No recorder history is available yet. Live values are still updating.</div>';return;}
  const points=series.flatMap(item=>item.points), minX=Math.min(...points.map(p=>p[0])), maxX=Math.max(...points.map(p=>p[0]));
  const units=[...new Set(series.map(item=>item.unit))],scales={};
  units.forEach(unit=>{const values=series.filter(item=>item.unit===unit).flatMap(item=>item.points.map(p=>p[1]));let min=Math.min(...values),max=Math.max(...values);if(unit==='%'){min=0;max=100}else if(unit==='V'){const pad=Math.max(.006,(max-min)*.18);min=Math.floor((min-pad)*1000)/1000;max=Math.ceil((max+pad)*1000)/1000}else{min=Math.min(0,min);max=Math.max(1,max*1.08)}scales[unit]={min,max};});
  const x=value=>70+(value-minX)/Math.max(1,maxX-minX)*870;
  const y=(value,unit)=>24+(scales[unit].max-value)/Math.max(.0001,scales[unit].max-scales[unit].min)*236;
  const paths=series.map((item,index)=>`<path class="chart-line" stroke="${chartColors[index%chartColors.length]}" d="${item.points.map((point,i)=>`${i?'L':'M'}${x(point[0]).toFixed(1)},${y(point[1],item.unit).toFixed(1)}`).join(' ')}"/>`).join('');
  const primary=units[0],secondary=units[1],grid=[0,.25,.5,.75,1].map(step=>{const yy=24+step*236,value=scales[primary].max-step*(scales[primary].max-scales[primary].min),right=secondary?scales[secondary].max-step*(scales[secondary].max-scales[secondary].min):null;return `<line x1="70" y1="${yy}" x2="940" y2="${yy}"/><text x="62" y="${yy+4}" text-anchor="end">${formatChartValue(value,primary)}</text>${secondary?`<text x="948" y="${yy+4}">${formatChartValue(right,secondary)}</text>`:''}`}).join('');
  const times=[0,.25,.5,.75,1].map(step=>{const xx=70+step*870,date=new Date(minX+step*(maxX-minX));return `<text x="${xx}" y="286" text-anchor="middle">${date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}</text>`}).join('');
  const legend=series.map((item,index)=>`<span><i style="--series:${chartColors[index%chartColors.length]}"></i>${item.name}</span>`).join('');
  host.innerHTML=`<div class="chart-legend">${legend}</div><div class="chart-canvas"><svg viewBox="0 0 1000 300" preserveAspectRatio="none" role="img" aria-label="${payload.chart} history"><g class="chart-grid-lines">${grid}${times}</g>${paths}<line class="chart-cursor" x1="0" x2="0" y1="24" y2="260" visibility="hidden"/></svg><div class="chart-tooltip"></div></div>`;
  attachChartPointer(host,series,minX,maxX,x);
}
function barChart(hostId,payload){
  const host=$(hostId),series=payload.series.filter(item=>item.points.length);
  if(!series.length){host.innerHTML='<div class="chart-empty">Daily energy totals will appear after recorder history accumulates.</div>';return;}
  const timestamps=[...new Set(series.flatMap(item=>item.points.map(p=>p[0])))].sort(),max=Math.max(1,...series.flatMap(item=>item.points.map(p=>p[1]))),groupWidth=870/Math.max(1,timestamps.length),barWidth=Math.min(26,groupWidth/(series.length+1));
  const bars=series.map((item,si)=>item.points.map(point=>{const di=timestamps.indexOf(point[0]),x=70+di*groupWidth+groupWidth/2+(si-(series.length-1)/2)*barWidth,y=260-point[1]/max*220;return `<rect x="${x-barWidth*.42}" y="${y}" width="${barWidth*.84}" height="${260-y}" rx="4" fill="${chartColors[si]}"/>`}).join('')).join('');
  const labels=timestamps.map((stamp,index)=>`<text x="${70+index*groupWidth+groupWidth/2}" y="286" text-anchor="middle">${new Date(stamp).toLocaleDateString([],{month:'short',day:'numeric'})}</text>`).join('');
  const grid=[0,.25,.5,.75,1].map(step=>{const y=260-step*220;return `<line x1="70" y1="${y}" x2="940" y2="${y}"/><text x="62" y="${y+4}" text-anchor="end">${(step*max).toFixed(1)}</text>`}).join('');
  host.innerHTML=`<div class="chart-legend">${series.map((item,index)=>`<span><i style="--series:${chartColors[index]}"></i>${item.name}</span>`).join('')}</div><div class="chart-canvas"><svg viewBox="0 0 1000 300" preserveAspectRatio="none" role="img" aria-label="Daily charged and discharged energy"><g class="chart-grid-lines">${grid}${labels}</g>${bars}</svg></div>`;
}
function attachChartPointer(host,series,minX,maxX,xScale){
  const canvas=host.querySelector('.chart-canvas'),svg=host.querySelector('svg'),cursor=host.querySelector('.chart-cursor'),tooltip=host.querySelector('.chart-tooltip');
  const inspect=event=>{const rect=svg.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),stamp=minX+ratio*(maxX-minX),values=series.map(item=>{const point=item.points.reduce((best,p)=>Math.abs(p[0]-stamp)<Math.abs(best[0]-stamp)?p:best,item.points[0]);return {...item,point};}),nearest=values[0].point[0],xx=xScale(nearest);cursor.setAttribute('x1',xx);cursor.setAttribute('x2',xx);cursor.setAttribute('visibility','visible');tooltip.innerHTML=`<b>${new Date(nearest).toLocaleString()}</b>${values.map((item,index)=>`<span><i style="--series:${chartColors[index%chartColors.length]}"></i>${item.name}: <strong>${formatChartValue(item.point[1],item.unit)}</strong></span>`).join('')}`;tooltip.classList.add('visible');tooltip.style.left=`${Math.min(68,Math.max(2,ratio*100))}%`;};
  canvas.addEventListener('pointermove',inspect);canvas.addEventListener('pointerdown',inspect);canvas.addEventListener('pointerleave',()=>{cursor.setAttribute('visibility','hidden');tooltip.classList.remove('visible')});
}
async function loadChart(name,renderer=lineChart){
  const status=$(`${name}-status`),hostId=`${name}-chart`;status.textContent='Loading…';
  try{const response=await fetch(`api/history?chart=${name}`,{cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);renderer(hostId,data);const count=data.series.reduce((sum,item)=>sum+item.points.length,0);status.textContent=count?`${data.cached?'Cached · ':''}${count.toLocaleString()} points`:'No history';}
  catch(error){status.textContent='History unavailable';$(hostId).innerHTML=`<div class="chart-empty">${error.message}</div>`;}
}
async function refreshTrends(){
  trendsLoadedAt=Date.now();$('chart-refresh').disabled=true;
  await Promise.all([loadChart('charging'),loadChart('battery1_cells'),loadChart('battery2_cells'),loadChart('multi_day',barChart)]);
  $('chart-refresh').disabled=false;
}

function value(readings,key,fallback='—'){
  const entry=readings[key];
  if(!entry||entry.value===null||entry.value===undefined)return fallback;
  return `${entry.value}${entry.unit?` ${entry.unit}`:''}`;
}
function numeric(readings,key){
  const raw=readings[key]?.value;
  const parsed=Number(raw);
  return Number.isFinite(parsed)?parsed:null;
}
function formatHours(hours){
  if(!Number.isFinite(hours))return null;
  if(hours<=0.05)return 'Now';
  const totalMinutes=Math.max(1,Math.round(hours*60));
  const days=Math.floor(totalMinutes/1440), remainder=totalMinutes%1440;
  const wholeHours=Math.floor(remainder/60), minutes=remainder%60;
  if(days)return `${days}d ${wholeHours}h`;
  if(wholeHours)return `${wholeHours}h ${minutes}m`;
  return `${minutes}m`;
}
function liveRuntime(readings,prefix,mode){
  const key=prefix==='bank_'
    ? (mode==='empty'?'bank_time_to_empty_current_load':'bank_time_to_full_current_input')
    : `${prefix}${mode==='empty'?'time_to_empty':'time_to_full'}`;
  const estimate=formatHours(numeric(readings,key));
  if(estimate)return estimate;
  const soc=numeric(readings,`${prefix}${prefix==='bank_'?'soc':'battery_soc'}`);
  if(mode==='full'&&soc!==null&&soc>=99.5)return 'Full';
  const direction=flowState(readings,prefix);
  if(direction==='waiting')return 'Calculating…';
  if(mode==='empty')return direction==='charging'?'Charging now':'Not discharging';
  return direction==='discharging'?'Not charging':'No charge input';
}
function flowState(readings,prefix='bank_'){
  const reported=readings[`${prefix}battery_status`]?.value||readings[`${prefix}status`]?.value||readings[`${prefix}flow_direction`]?.value;
  if(['charging','discharging','idle'].includes(reported))return reported;
  const current=numeric(readings,`${prefix}battery_current`)??numeric(readings,`${prefix}current`);
  if(current===null)return 'waiting';
  return current < -0.05?'charging':current > 0.05?'discharging':'idle';
}
function flowValue(readings,prefix,key){
  const reading=readings[`${prefix}${key}`];
  if(!reading||reading.value===null||reading.value===undefined)return '—';
  const amount=Number(reading.value);
  if(!Number.isFinite(amount))return value(readings,`${prefix}${key}`);
  const direction=flowState(readings,prefix);
  const suffix=key==='battery_current'||key==='current'
    ? (direction==='charging'?'charging':direction==='discharging'?'discharging':'idle')
    : (direction==='charging'?'into battery':direction==='discharging'?'to loads':'idle');
  return `${Math.abs(amount).toFixed(1)} ${reading.unit||''} ${suffix}`.trim();
}
function age(iso){
  if(!iso)return 'Never';
  const seconds=Math.max(0,Math.round((Date.now()-new Date(iso).getTime())/1000));
  if(seconds<2)return 'Just now';
  if(seconds<60)return `${seconds}s ago`;
  if(seconds<3600)return `${Math.floor(seconds/60)}m ago`;
  return new Date(iso).toLocaleString();
}
function duration(seconds){
  if(seconds<60)return `${seconds}s`;
  if(seconds<3600)return `${Math.floor(seconds/60)}m ${seconds%60}s`;
  return `${Math.floor(seconds/3600)}h ${Math.floor((seconds%3600)/60)}m`;
}
function healthCopy(health,data={}){
  const link=data.transport||'CAN';
  return {
    healthy:[`Live ${link} traffic`,'Healthy','healthy'],
    attention:['Both batteries online · balance needs attention','Attention','warning'],
    no_traffic:[`Adapter ready · no valid ${link} replies`,'No traffic','warning'],
    stale:[`${link} traffic stopped`,'Stale','warning'],
    adapter_missing:[`${link} adapter unavailable`,'Adapter missing','error']
  }[health]||['Checking monitor','Starting','warning'];
}
function checksFor(health,data={}){
  if(data.transport==='RS485'&&health==='adapter_missing')return ['Reconnect the USB RS485 cable.','Confirm the FTDI adapter appears under /dev/serial/by-id.','Keep the connection set to Felicity RS485, then restart the app.'];
  if(data.transport==='RS485'&&health==='no_traffic')return ['Confirm the battery master is powered and its Modbus address is configured.','Verify battery pin 5 reaches RS485-B and pin 6 reaches RS485-A.','Swap A/B at the adapter if its A/B convention is opposite.','Keep the link at 9600 baud, 8N1.'];
  if(data.transport==='RS485'&&health==='stale')return ['Check whether the battery restarted.','Inspect the RS485 cable and battery master port.','Review the most recent valid register response below.'];
  if(data.transport==='RS485'&&health==='attention')return ['Both battery addresses are online.','Allow Battery 2 time to charge and balance.','Watch the SOC difference and maximum cell spread; investigate if either continues increasing.'];
  if(data.transport==='RS485')return ['USB RS485 adapter is connected.','CRC-valid Modbus replies are arriving from both battery addresses.','Per-pack and combined bank values are updating in Home Assistant.'];
  if(health==='adapter_missing')return ['Reconnect the CANable USB cable.','Confirm the device appears as /dev/ttyACM0 or gs_usb.','Keep adapter mode set to Auto, then restart the app.'];
  if(health==='no_traffic'&&data.bus_mode==='standalone_ack')return ['Confirm the battery master is powered and its CAN output is enabled.','Keep the CANable 120Ω termination switch ON for the standalone endpoint.','Verify CAN-H and CAN-L reach the correct RJ45 pins.','Confirm the configured bit rate is 500 kbit/s.'];
  if(health==='no_traffic')return ['Confirm inverter and battery master are powered.','Keep the CANable 120Ω termination switch OFF.','Verify CAN-H and CAN-L reach the correct RJ45 pins.','Confirm the configured bit rate is 500 kbit/s.'];
  if(health==='stale')return ['Check whether the inverter or battery master restarted.','Inspect the CAN cable and both monitoring-tap terminals.','Review the most recent frame ID and timestamp below.'];
  return ['USB adapter is connected.','Valid CAN frames are arriving.','Decoded battery values are updating in Home Assistant.'];
}
function renderBattery(readings){
  const addresses=latest?.battery_addresses?.length?latest.battery_addresses:[1,2];
  const bankCards=[['Bank SOC','bank_soc'],['Available energy','bank_remaining_energy'],['Time until empty','runtime_empty'],['Time until full','runtime_full'],['SOC difference','bank_soc_difference'],['Largest cell spread','bank_maximum_cell_spread']];
  replaceHtml('bank-summary',bankCards.map(([label,key])=>`<article class="reading-card"><small>${label.toUpperCase()}</small><h3>${key==='runtime_empty'?liveRuntime(readings,'bank_','empty'):key==='runtime_full'?liveRuntime(readings,'bank_','full'):value(readings,key)}</h3><p>${key==='runtime_empty'?'At current net load':key==='runtime_full'?'At current net input':'Combined battery bank'}</p></article>`).join(''));
  const sections=addresses.map(address=>{
    const prefix=`battery_${address}_`, online=readings[`${prefix}online`]?.value==='on';
    const balance=readings[`${prefix}cell_balance`]?.value||'waiting';
    const statusClass=!online?'':balance==='attention'?'attention':'online';
    const direction=flowState(readings,prefix);
    const directionLabel=direction==='charging'?'CHARGING ↓':direction==='discharging'?'DISCHARGING ↑':direction==='idle'?'IDLE':'WAITING';
    const metrics=[['State of charge','battery_soc'],['Voltage','battery_voltage'],['Flow',null],['Current','battery_current'],['Power','battery_power'],['Time to empty','runtime_empty'],['Time to full','runtime_full'],['Pack temperature','pack_temperature'],['Available energy','remaining_energy'],['Cell spread','cell_voltage_difference'],['BMS version','bms_version']];
    const cells=[];
    for(let i=1;i<=16;i++)if(readings[`${prefix}cell_${i}_voltage`])cells.push({number:i,...readings[`${prefix}cell_${i}_voltage`]});
    return `<article class="panel battery-pack"><div class="pack-title"><div class="pack-title-copy"><small class="pack-kicker">FELICITY LPBA48100-OL · ADDRESS ${address}</small><h3>Battery ${address}</h3><span>51.2 V · 100 Ah · 5.12 kWh</span></div><span class="pack-status ${statusClass}">${online?(balance==='attention'?'BALANCE ATTENTION':'ONLINE'):'OFFLINE'}</span></div><div class="pack-flow ${direction}">${directionLabel}</div><div class="pack-metrics">${metrics.map(([label,key])=>`<div class="pack-metric"><small>${label.toUpperCase()}</small><b>${key===null?directionLabel:key==='runtime_empty'?liveRuntime(readings,prefix,'empty'):key==='runtime_full'?liveRuntime(readings,prefix,'full'):key==='battery_current'||key==='battery_power'?flowValue(readings,prefix,key):value(readings,prefix+key)}</b></div>`).join('')}</div><div class="panel-head cells-panel"><div><p>CELL BALANCE</p><h3>Battery ${address} cell voltages</h3></div><span>${value(readings,prefix+'cell_voltage_difference','No data')}</span></div><div class="cell-grid">${cells.length?cells.map(cell=>`<div class="cell ${balance==='attention'?'attention':''}"><small>CELL ${cell.number}</small><b>${cell.value} ${cell.unit||'V'}</b></div>`).join(''):'<div class="empty">Waiting for register 0x132A.</div>'}</div></article>`;
  }).join('');
  replaceHtml('battery-pack-sections',sections);

  replaceHtml('pack-overview-grid',addresses.map(address=>{
    const prefix=`battery_${address}_`, online=readings[`${prefix}online`]?.value==='on', balance=readings[`${prefix}cell_balance`]?.value||'waiting';
    const direction=flowState(readings,prefix), directionLabel=direction==='charging'?'CHARGING ↓':direction==='discharging'?'DISCHARGING ↑':direction==='idle'?'IDLE':'WAITING';
    return `<article class="pack-overview"><div class="pack-overview-head"><div><small class="pack-kicker">BATTERY ${address} · ADDRESS ${address}</small><h3>${value(readings,prefix+'battery_soc','Waiting')}</h3></div><span class="pack-status ${online?(balance==='attention'?'attention':'online'):''}">${online?(balance==='attention'?'ATTENTION':'ONLINE'):'OFFLINE'}</span></div><div class="pack-flow ${direction}">${directionLabel}</div><div class="pack-overview-values"><div><small>VOLTAGE</small><b>${value(readings,prefix+'battery_voltage')}</b></div><div><small>CURRENT</small><b>${flowValue(readings,prefix,'battery_current')}</b></div><div><small>CELLS</small><b>${value(readings,prefix+'cell_voltage_difference')}</b></div><div><small>TEMP</small><b>${value(readings,prefix+'pack_temperature')}</b></div></div></article>`;
  }).join(''));
}
function renderRecovery(readings,data){
  const state=readings.recovery_state?.value||'learning_limits';
  const safe=readings.recovery_charge_safe?.value==='on';
  const full=readings.recovery_full_rate_safe?.value==='on';
  const labels={normal:'Normal charging permitted',recovery:'Low-rate recovery required',charge_blocked:'Charging blocked for safety',monitoring_unavailable:'Monitoring incomplete',learning_limits:'Learning BMS limits'};
  $('recovery-title').textContent=labels[state]||String(state).replaceAll('_',' ');
  $('recovery-summary').textContent=value(readings,'recovery_summary','Waiting for a complete assessment.');
  $('recovery-action').textContent=value(readings,'recovery_action','Automatic control remains locked.');
  $('recovery-limit').textContent=value(readings,'recovery_recommended_charge_limit');
  $('recovery-badge').textContent=state==='normal'?'READY':state==='charge_blocked'?'BLOCKED':'RECOVERY';
  $('recovery-badge').className=`badge ${state==='normal'?'neutral':'warning'}`;
  $('recovery-hero').className=`recovery-hero ${state}`;
  $('charge-safe').textContent=safe?'CHARGE SAFE':'CHARGE LOCKED';
  $('charge-safe').className=`status-badge ${safe?'healthy':'error'}`;
  $('full-rate-state').textContent=full?'Full rate permitted':'Full rate locked';
  $('full-rate-detail').textContent=full?'Permitted within BMS and wiring limits':'Locked until cells and pack SOC are balanced';
  $('weakest-battery').textContent=value(readings,'recovery_weakest_battery');
  $('weakest-cell').textContent=value(readings,'recovery_weakest_cell');
  const control=data.recovery_control||{};
  $('controller-enabled').textContent=control.enabled?'AUTOMATIC':'MONITOR ONLY';
  $('controller-enabled').className=`status-badge ${control.enabled?'healthy':'warning'}`;
  $('generator-switch').textContent=`${control.switch_state||'unknown'} · ${control.switch_entity||'not configured'}`;
  $('generator-voltage').textContent=control.generator_voltage===null||control.generator_voltage===undefined?'Unavailable':`${control.generator_voltage} V`;
  $('controller-action').textContent=control.last_action||'none';
  $('controller-note').textContent=control.error||(!control.enabled?'Automatic actions remain locked until enabled in app configuration.':'Guarded automatic control is active with anti-cycling protection.');
  const addresses=data.battery_addresses?.length?data.battery_addresses:[1,2];
  replaceHtml('limit-grid',addresses.map(address=>{
    const p=`battery_${address}_`;
    return `<article class="panel limit-card"><div class="panel-head"><div><p>BMS OPERATING ENVELOPE</p><h3>Battery ${address}</h3></div><span class="pack-status ${readings[p+'charge_allowed']?.value==='on'?'online':'attention'}">${readings[p+'charge_allowed']?.value==='on'?'CHARGE ALLOWED':'CHARGE BLOCKED'}</span></div><div class="limit-values"><div><small>CHARGE CURRENT</small><b>${value(readings,p+'charge_current_limit')}</b></div><div><small>CHARGE VOLTAGE</small><b>${value(readings,p+'charge_voltage_limit')}</b></div><div><small>DISCHARGE CURRENT</small><b>${value(readings,p+'discharge_current_limit')}</b></div><div><small>DISCHARGE VOLTAGE</small><b>${value(readings,p+'discharge_voltage_limit')}</b></div></div></article>`;
  }).join(''));
}
function renderFrames(frames){
  replaceHtml('frame-list',frames.length?frames.map(frame=>`<div class="frame-row"><b>${frame.id}</b><code>${frame.data}</code><small>${age(frame.at)}</small></div>`).join(''):'<div class="empty">No frames received yet.</div>');
}
const idNames={'0x311':'Charge and discharge limits','0x312':'Protection and alarm flags','0x313':'Voltage, current, SOC and temperature','0x314':'Capacity, cell spread and cycles','0x315':'Cell voltages 1–4','0x316':'Cell voltages 5–8','0x317':'Cell voltages 9–12','0x318':'Cell voltages 13–16','0x319':'Battery requests and cell extremes','0x320':'Battery manufacturer and versions'};
function renderTraffic(data){
  const rs485=data.transport==='RS485', noun=rs485?'replies':'frames';
  const fps=Number(data.frames_per_second||0), ids=data.traffic_ids||[], frames=data.recent_frames||[];
  $('traffic-rate').textContent=`${fps.toFixed(1)} ${noun}/s`;
  $('traffic-copy').textContent=data.bus_active?`Valid ${data.transport||'CAN'} messages are arriving now.`:`Waiting for valid ${data.transport||'CAN'} traffic.`;
  $('traffic-pulse').classList.toggle('live',data.bus_active);
  $('traffic-badge').textContent=data.bus_active?'LIVE':'WAITING';
  $('traffic-badge').className=`badge ${data.bus_active?'':'warning'}`;
  $('minute-frames').textContent=`${Number(data.frames_last_minute||0).toLocaleString()} ${noun}`;
  $('traffic-meter').style.width=`${Math.min(100,Number(data.frames_last_minute||0)/6)}%`;
  $('id-count').textContent=`${ids.length} ${rs485?'register':'ID'}${ids.length===1?'':'s'}`;
  replaceHtml('traffic-id-grid',ids.length?ids.map(item=>`<div class="traffic-id"><b>${item.id}</b><span>${idNames[item.id]||(rs485?'Felicity Modbus response':'Unmapped CAN message')}</span><small>${Number(item.count).toLocaleString()} received</small></div>`).join(''):`<div class="empty">No ${rs485?'Modbus replies':'CAN identifiers'} received yet.</div>`);
  replaceHtml('traffic-frame-list',frames.length?frames.map(frame=>`<div class="traffic-frame"><b>${frame.id}</b><code>${frame.data}</code><span>${(frame.decoded||[]).map(key=>key.replaceAll('_',' ')).join(', ')||'Raw frame'}</span><small>${age(frame.at)}</small></div>`).join(''):'<div class="empty">No frames received yet.</div>');
}
function renderDeviceLights(data){
  const adapter=Boolean(data.adapter_connected), traffic=Boolean(data.bus_active), rs485=data.transport==='RS485';
  $('led-pwr').classList.toggle('on',adapter);
  $('led-state').classList.toggle('on',traffic);
  $('led-state').classList.toggle('pulse',traffic);
  $('led-work').classList.toggle('on',traffic);
  $('led-work').classList.toggle('pulse',traffic);
  $('led-pwr-state').textContent=adapter?'On':'Off';
  $('device-model').textContent=rs485?'IOCREST FS-422/485':'CANABLE V2.0 PRO';
  $('led-state-label').textContent=rs485?'RXD':'STATE';
  $('led-work-label').textContent=rs485?'TXD':'WORK';
  $('led-state-state').textContent=traffic?(rs485?'Reply':'Connected'):'Idle';
  $('led-work-state').textContent=traffic?(rs485?'Polling':'Receiving'):'Waiting';
  $('device-lights-status').textContent=!adapter?'Adapter not detected — indicator state unavailable':traffic?(rs485?'ACTIVE steady · RXD/TXD pulse with each battery poll':'PWR and STATE steady · WORK pulses with incoming frames'):(rs485?'ACTIVE on · waiting for battery replies':'PWR and STATE active · WORK waiting for CAN traffic');
}
function render(data){
  latest=data;
  const readings=data.readings||{};
  const displayHealth=data.health==='healthy'&&readings.bank_health?.value==='attention'?'attention':data.health;
  const [title,badge,badgeClass]=healthCopy(displayHealth,data);
  const diagnosis=data.diagnosis;
  $('hero-status').textContent=title;
  $('health-title').textContent=title;
  $('diagnostic-title').textContent=title;
  $('diagnosis').textContent=diagnosis;
  $('diagnostic-finding').textContent=diagnosis;
  $('health-badge').textContent=badge;
  $('health-badge').className=`status-badge ${badgeClass}`;
  $('health-orbit').className=badgeClass;
  ['hero-light','side-light'].forEach(id=>$(id).className=displayHealth==='healthy'?'online':data.health==='adapter_missing'?'error':'');
  $('side-status').textContent=data.health==='healthy'?'Live battery replies arriving':badge;
  $('adapter-summary').textContent=data.adapter_connected?'Connected':'Not ready';
  $('bus-summary').textContent=data.bus_active?'Live':data.frames_received?'Stopped':'No frames';
  $('bitrate-summary').textContent=data.transport==='RS485'?`${Number(data.bitrate||0)} baud`:`${Number(data.bitrate||0)/1000} kbit/s`;
  $('frames-summary').textContent=Number(data.frames_received||0).toLocaleString();
  $('soc').textContent=value(readings,'bank_soc',value(readings,'battery_soc'));
  $('battery-state').textContent=`${value(readings,'bank_online_batteries','0')} of ${value(readings,'bank_configured_batteries','2')} batteries online · ${value(readings,'bank_remaining_energy','calculating')}`;
  const flow=flowState(readings,'bank_');
  const flowPower=numeric(readings,'bank_power')??numeric(readings,'battery_power');
  const flowCurrent=numeric(readings,'bank_current')??numeric(readings,'battery_current');
  const flowCopy={charging:['↓','CHARGING','into batteries'],discharging:['↑','DISCHARGING','supplying loads'],idle:['→','IDLE','no significant flow'],waiting:['·','WAITING','waiting for data']}[flow];
  $('flow-card').className=`metric-card flow-card ${flow}`;
  $('flow-arrow').textContent=flowCopy[0];
  $('flow-state').textContent=flowCopy[1];
  $('power').textContent=flowPower===null?'—':`${Math.abs(flowPower).toFixed(1)} W ${flowCopy[2]}`;
  $('voltage').textContent=value(readings,'bank_voltage',value(readings,'battery_voltage'));
  $('current').textContent=flowCurrent===null?'—':`${Math.abs(flowCurrent).toFixed(1)} A`;
  $('time-to-empty').textContent=liveRuntime(readings,'bank_','empty');
  $('time-to-full').textContent=liveRuntime(readings,'bank_','full');
  const dischargePower=numeric(readings,'bank_discharging_power'),chargePower=numeric(readings,'bank_charging_power');
  $('time-to-empty-detail').textContent=dischargePower>5?`At the current ${dischargePower.toFixed(0)} W net load`:'Updates automatically when the bank is discharging';
  $('time-to-full-detail').textContent=chargePower>5?`At the current ${chargePower.toFixed(0)} W net input · tapering may extend this`:'Updates automatically when charging begins';
  const alarm=readings.alarm_active?.value==='on', protection=readings.protection_active?.value==='on';
  const bankAttention=readings.bank_health?.value==='attention';
  $('safety').textContent=alarm||protection||bankAttention?'Attention':'Normal';
  $('safety-detail').textContent=protection?value(readings,'protection_flags'):alarm?value(readings,'alarm_flags'):bankAttention?`${value(readings,'bank_soc_difference')} SOC difference · ${value(readings,'bank_maximum_cell_spread')} max cell spread`:'Both batteries balanced and online';
  $('battery-badge').textContent=data.bus_active?'Live':'Waiting';
  $('service-detail').textContent=data.service||'—';
  $('adapter-detail').textContent=data.adapter||'—';
  $('bitrate-detail').textContent=`${Number(data.bitrate||0).toLocaleString()} ${data.transport==='RS485'?'baud':'bit/s'}`;
  $('last-id').textContent=data.last_id||'—';
  $('last-frame').textContent=age(data.last_frame_at);
  $('uptime').textContent=duration(data.uptime_seconds||0);
  $('frame-count').textContent=`${Number(data.frames_received||0).toLocaleString()} total`;
  replaceHtml('check-list',checksFor(displayHealth,data).map((item,index)=>`<div class="check"><i>${index+1}</i><span>${item}</span></div>`).join(''));
  $('last-check').textContent=`Updated ${new Date().toLocaleTimeString()}`;
  renderBattery(readings);renderRecovery(readings,data);renderFrames(data.recent_frames||[]);renderTraffic(data);renderDeviceLights(data);
}
async function refresh(){
  if(refreshTimer){clearTimeout(refreshTimer);refreshTimer=null;}
  $('refresh').disabled=true;
  try{
    const response=await fetch('api/status',{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  }catch(error){
    $('hero-status').textContent='Status page disconnected';
    $('diagnosis').textContent='The dashboard cannot reach the local monitor status API. Restart the app and reload this page.';
    ['hero-light','side-light'].forEach(id=>$(id).className='error');
    renderDeviceLights({adapter_connected:false,bus_active:false});
  }finally{
    $('refresh').disabled=false;
    refreshTimer=setTimeout(refresh,document.hidden?30000:3000);
  }
}
$('refresh').addEventListener('click',refresh);
$('chart-refresh').addEventListener('click',()=>{trendsLoadedAt=0;refreshTrends()});
$('emergency-stop').addEventListener('click',async()=>{
  if(!confirm('Open the configured generator input switch now? This removes generator AC from the inverter.'))return;
  $('emergency-stop').disabled=true;
  try{
    const response=await fetch('api/recovery/emergency-stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const result=await response.json();
    if(!response.ok||!result.ok)throw new Error(result.error||`HTTP ${response.status}`);
    $('controller-note').textContent='Generator input switch opened by operator request.';
    await refresh();
  }catch(error){$('controller-note').textContent=`Emergency stop failed: ${error.message}`}
  finally{$('emergency-stop').disabled=false;}
});
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden)refresh();
});
refresh();
