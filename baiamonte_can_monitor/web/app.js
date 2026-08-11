const pages = [...document.querySelectorAll('.page')];
const navButtons = [...document.querySelectorAll('.nav')];
const titles = {overview:'Overview',battery:'Battery data',traffic:'CAN traffic',diagnostics:'Diagnostics',wiring:'Wiring help'};
const $ = id => document.getElementById(id);
let latest = null;

function showPage(id){
  pages.forEach(page=>page.classList.toggle('active',page.id===id));
  navButtons.forEach(button=>button.classList.toggle('active',button.dataset.page===id));
  $('page-title').textContent=titles[id]||'CAN Monitor';
  navButtons.find(button=>button.dataset.page===id)?.scrollIntoView({behavior:'smooth',block:'nearest',inline:'center'});
  window.scrollTo({top:0,behavior:'smooth'});
}
navButtons.forEach(button=>button.addEventListener('click',()=>showPage(button.dataset.page)));
document.querySelectorAll('[data-go]').forEach(button=>button.addEventListener('click',()=>showPage(button.dataset.go)));

function value(readings,key,fallback='—'){
  const entry=readings[key];
  if(!entry||entry.value===null||entry.value===undefined)return fallback;
  return `${entry.value}${entry.unit?` ${entry.unit}`:''}`;
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
function healthCopy(health){
  return {
    healthy:['Live CAN traffic','Healthy','healthy'],
    no_traffic:['Adapter ready · no frames','No traffic','warning'],
    stale:['CAN traffic stopped','Stale','warning'],
    adapter_missing:['CAN adapter unavailable','Adapter missing','error']
  }[health]||['Checking monitor','Starting','warning'];
}
function checksFor(health){
  if(health==='adapter_missing')return ['Reconnect the CANable USB cable.','Confirm the device appears as /dev/ttyACM0 or gs_usb.','Keep adapter mode set to Auto, then restart the app.'];
  if(health==='no_traffic')return ['Confirm inverter and battery master are powered.','Keep the CANable 120Ω termination switch OFF.','Verify CAN-H and CAN-L reach the correct RJ45 pins.','Confirm the configured bit rate is 500 kbit/s.'];
  if(health==='stale')return ['Check whether the inverter or battery master restarted.','Inspect the CAN cable and both monitoring-tap terminals.','Review the most recent frame ID and timestamp below.'];
  return ['USB adapter is connected.','Valid CAN frames are arriving.','Decoded battery values are updating in Home Assistant.'];
}
function renderBattery(readings){
  const cards=[
    ['Battery voltage','battery_voltage','Frame 0x313'],['Battery current','battery_current','Frame 0x313'],['Battery power','battery_power','Calculated'],['State of charge','battery_soc','Frame 0x313'],
    ['State of health','battery_soh','Frame 0x313'],['Battery status','battery_status','Frame 0x311'],['Maximum temperature','maximum_cell_temperature','Frame 0x313'],['Cycle count','cycle_count','Frame 0x314'],
    ['Remaining capacity','remaining_capacity','Frame 0x314'],['Full capacity','full_charge_capacity','Frame 0x314'],['Charge limit','charge_current_limit','Frame 0x311'],['Discharge limit','discharge_current_limit','Frame 0x311'],
    ['Charge voltage limit','charge_voltage_limit','Frame 0x311'],['Cell difference','cell_voltage_difference','Frame 0x314'],['Chemistry','battery_chemistry','Frame 0x319'],['Manufacturer','battery_manufacturer','Frame 0x320']
  ];
  $('battery-grid').innerHTML=cards.map(([label,key,source])=>`<article class="reading-card"><small>${label.toUpperCase()}</small><h3>${value(readings,key)}</h3><p>${source}</p></article>`).join('');
  const cells=[];
  for(let i=1;i<=16;i++)if(readings[`cell_${i}_voltage`])cells.push({number:i,...readings[`cell_${i}_voltage`]});
  $('cell-grid').innerHTML=cells.length?cells.map(cell=>`<div class="cell"><small>CELL ${cell.number}</small><b>${cell.value} ${cell.unit||'V'}</b></div>`).join(''):'<div class="empty">Waiting for cell-voltage frames 0x315–0x318.</div>';
  if(cells.length){const values=cells.map(cell=>Number(cell.value));$('cell-spread').textContent=`${((Math.max(...values)-Math.min(...values))*1000).toFixed(0)} mV spread`;}
}
function renderFrames(frames){
  $('frame-list').innerHTML=frames.length?frames.map(frame=>`<div class="frame-row"><b>${frame.id}</b><code>${frame.data}</code><small>${age(frame.at)}</small></div>`).join(''):'<div class="empty">No frames received yet.</div>';
}
const idNames={'0x311':'Charge and discharge limits','0x312':'Protection and alarm flags','0x313':'Voltage, current, SOC and temperature','0x314':'Capacity, cell spread and cycles','0x315':'Cell voltages 1–4','0x316':'Cell voltages 5–8','0x317':'Cell voltages 9–12','0x318':'Cell voltages 13–16','0x319':'Battery requests and cell extremes','0x320':'Battery manufacturer and versions'};
function renderTraffic(data){
  const fps=Number(data.frames_per_second||0), ids=data.traffic_ids||[], frames=data.recent_frames||[];
  $('traffic-rate').textContent=`${fps.toFixed(1)} frames/s`;
  $('traffic-copy').textContent=data.bus_active?'Valid CAN messages are arriving now.':'Waiting for valid 500 kbit/s CAN traffic.';
  $('traffic-pulse').classList.toggle('live',data.bus_active);
  $('traffic-badge').textContent=data.bus_active?'LIVE':'WAITING';
  $('traffic-badge').className=`badge ${data.bus_active?'':'warning'}`;
  $('minute-frames').textContent=`${Number(data.frames_last_minute||0).toLocaleString()} frames`;
  $('traffic-meter').style.width=`${Math.min(100,Number(data.frames_last_minute||0)/6)}%`;
  $('id-count').textContent=`${ids.length} ID${ids.length===1?'':'s'}`;
  $('traffic-id-grid').innerHTML=ids.length?ids.map(item=>`<div class="traffic-id"><b>${item.id}</b><span>${idNames[item.id]||'Unmapped CAN message'}</span><small>${Number(item.count).toLocaleString()} received</small></div>`).join(''):'<div class="empty">No CAN identifiers received yet.</div>';
  $('traffic-frame-list').innerHTML=frames.length?frames.map(frame=>`<div class="traffic-frame"><b>${frame.id}</b><code>${frame.data}</code><span>${(frame.decoded||[]).map(key=>key.replaceAll('_',' ')).join(', ')||'Raw frame'}</span><small>${age(frame.at)}</small></div>`).join(''):'<div class="empty">No frames received yet.</div>';
}
function renderDeviceLights(data){
  const adapter=Boolean(data.adapter_connected), traffic=Boolean(data.bus_active);
  $('led-pwr').classList.toggle('on',adapter);
  $('led-state').classList.toggle('on',adapter);
  $('led-work').classList.toggle('on',traffic);
  $('led-work').classList.toggle('pulse',traffic);
  $('led-pwr-state').textContent=adapter?'On':'Off';
  $('led-state-state').textContent=adapter?'Connected':'Idle';
  $('led-work-state').textContent=traffic?'Receiving':'Waiting';
  $('device-lights-status').textContent=!adapter?'Adapter not detected — indicator state unavailable':traffic?'PWR and STATE steady · WORK pulses with incoming frames':'PWR and STATE active · WORK waiting for CAN traffic';
}
function render(data){
  latest=data;
  const readings=data.readings||{};
  const [title,badge,badgeClass]=healthCopy(data.health);
  $('hero-status').textContent=title;
  $('health-title').textContent=title;
  $('diagnostic-title').textContent=title;
  $('diagnosis').textContent=data.diagnosis;
  $('diagnostic-finding').textContent=data.diagnosis;
  $('health-badge').textContent=badge;
  $('health-badge').className=`status-badge ${badgeClass}`;
  $('health-orbit').className=badgeClass;
  ['hero-light','side-light'].forEach(id=>$(id).className=data.health==='healthy'?'online':data.health==='adapter_missing'?'error':'');
  $('side-status').textContent=data.health==='healthy'?'Live frames arriving':badge;
  $('adapter-summary').textContent=data.adapter_connected?'Connected':'Not ready';
  $('bus-summary').textContent=data.bus_active?'Live':data.frames_received?'Stopped':'No frames';
  $('bitrate-summary').textContent=`${Number(data.bitrate||0)/1000} kbit/s`;
  $('frames-summary').textContent=Number(data.frames_received||0).toLocaleString();
  $('soc').textContent=value(readings,'battery_soc');
  $('battery-state').textContent=value(readings,'battery_status','Waiting for frame 0x311');
  $('power').textContent=value(readings,'battery_power');
  $('voltage').textContent=value(readings,'battery_voltage');
  $('current').textContent=value(readings,'battery_current');
  const alarm=readings.alarm_active?.value==='on', protection=readings.protection_active?.value==='on';
  $('safety').textContent=alarm||protection?'Attention':'Normal';
  $('safety-detail').textContent=protection?value(readings,'protection_flags'):alarm?value(readings,'alarm_flags'):readings.protection_active?'No active flags':'Waiting for protection flags';
  $('battery-badge').textContent=data.bus_active?'Live':'Waiting';
  $('service-detail').textContent=data.service||'—';
  $('adapter-detail').textContent=data.adapter||'—';
  $('bitrate-detail').textContent=`${Number(data.bitrate||0).toLocaleString()} bit/s`;
  $('last-id').textContent=data.last_id||'—';
  $('last-frame').textContent=age(data.last_frame_at);
  $('uptime').textContent=duration(data.uptime_seconds||0);
  $('frame-count').textContent=`${Number(data.frames_received||0).toLocaleString()} total`;
  $('check-list').innerHTML=checksFor(data.health).map((item,index)=>`<div class="check"><i>${index+1}</i><span>${item}</span></div>`).join('');
  $('last-check').textContent=`Updated ${new Date().toLocaleTimeString()}`;
  renderBattery(readings);renderFrames(data.recent_frames||[]);renderTraffic(data);renderDeviceLights(data);
}
async function refresh(){
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
  }finally{$('refresh').disabled=false;}
}
$('refresh').addEventListener('click',refresh);
refresh();setInterval(refresh,3000);
