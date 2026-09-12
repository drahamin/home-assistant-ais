const pages = [...document.querySelectorAll('.page')];
const navButtons = [...document.querySelectorAll('.nav')];
const titles = {overview:'Overview',battery:'Battery data',traffic:'Bus traffic',diagnostics:'Diagnostics',wiring:'Wiring help'};
const $ = id => document.getElementById(id);
let latest = null;
let refreshTimer = null;
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
  const bankCards=[['Bank SOC','bank_soc'],['Available energy','bank_remaining_energy'],['Nominal storage','bank_nominal_energy'],['SOC difference','bank_soc_difference'],['Largest cell spread','bank_maximum_cell_spread']];
  replaceHtml('bank-summary',bankCards.map(([label,key])=>`<article class="reading-card"><small>${label.toUpperCase()}</small><h3>${value(readings,key)}</h3><p>Combined battery bank</p></article>`).join(''));
  const sections=addresses.map(address=>{
    const prefix=`battery_${address}_`, online=readings[`${prefix}online`]?.value==='on';
    const balance=readings[`${prefix}cell_balance`]?.value||'waiting';
    const statusClass=!online?'':balance==='attention'?'attention':'online';
    const metrics=[['State of charge','battery_soc'],['Voltage','battery_voltage'],['Current','battery_current'],['Power','battery_power'],['Pack temperature','pack_temperature'],['Available energy','remaining_energy'],['Cell spread','cell_voltage_difference'],['BMS version','bms_version']];
    const cells=[];
    for(let i=1;i<=16;i++)if(readings[`${prefix}cell_${i}_voltage`])cells.push({number:i,...readings[`${prefix}cell_${i}_voltage`]});
    return `<article class="panel battery-pack"><div class="pack-title"><div class="pack-title-copy"><small class="pack-kicker">FELICITY LPBA48100-OL · ADDRESS ${address}</small><h3>Battery ${address}</h3><span>51.2 V · 100 Ah · 5.12 kWh</span></div><span class="pack-status ${statusClass}">${online?(balance==='attention'?'BALANCE ATTENTION':'ONLINE'):'OFFLINE'}</span></div><div class="pack-metrics">${metrics.map(([label,key])=>`<div class="pack-metric"><small>${label.toUpperCase()}</small><b>${value(readings,prefix+key)}</b></div>`).join('')}</div><div class="panel-head cells-panel"><div><p>CELL BALANCE</p><h3>Battery ${address} cell voltages</h3></div><span>${value(readings,prefix+'cell_voltage_difference','No data')}</span></div><div class="cell-grid">${cells.length?cells.map(cell=>`<div class="cell ${balance==='attention'?'attention':''}"><small>CELL ${cell.number}</small><b>${cell.value} ${cell.unit||'V'}</b></div>`).join(''):'<div class="empty">Waiting for register 0x132A.</div>'}</div></article>`;
  }).join('');
  replaceHtml('battery-pack-sections',sections);

  replaceHtml('pack-overview-grid',addresses.map(address=>{
    const prefix=`battery_${address}_`, online=readings[`${prefix}online`]?.value==='on', balance=readings[`${prefix}cell_balance`]?.value||'waiting';
    return `<article class="pack-overview"><div class="pack-overview-head"><div><small class="pack-kicker">BATTERY ${address} · ADDRESS ${address}</small><h3>${value(readings,prefix+'battery_soc','Waiting')}</h3></div><span class="pack-status ${online?(balance==='attention'?'attention':'online'):''}">${online?(balance==='attention'?'ATTENTION':'ONLINE'):'OFFLINE'}</span></div><div class="pack-overview-values"><div><small>VOLTAGE</small><b>${value(readings,prefix+'battery_voltage')}</b></div><div><small>CURRENT</small><b>${value(readings,prefix+'battery_current')}</b></div><div><small>CELLS</small><b>${value(readings,prefix+'cell_voltage_difference')}</b></div><div><small>TEMP</small><b>${value(readings,prefix+'pack_temperature')}</b></div></div></article>`;
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
  $('power').textContent=value(readings,'bank_power',value(readings,'battery_power'));
  $('voltage').textContent=value(readings,'bank_voltage',value(readings,'battery_voltage'));
  $('current').textContent=value(readings,'bank_current',value(readings,'battery_current'));
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
  renderBattery(readings);renderFrames(data.recent_frames||[]);renderTraffic(data);renderDeviceLights(data);
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
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden)refresh();
});
refresh();
