const $=selector=>document.querySelector(selector),$$=selector=>[...document.querySelectorAll(selector)];
const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const fmt=value=>value===null||value===undefined||value===''?'—':value;
const ago=value=>{if(!value)return'just now';const seconds=Math.max(0,(Date.now()-new Date(value).getTime())/1000);if(seconds<60)return`${Math.floor(seconds)}s ago`;if(seconds<3600)return`${Math.floor(seconds/60)}m ago`;return`${Math.floor(seconds/3600)}h ago`};
let state=null,refreshRunning=false,refreshFailures=0,selectedMapMmsi=null;
let mapDisplayMode='labels';
try{mapDisplayMode=localStorage.getItem('baiamonteAisMapDisplay')==='focus'?'focus':'labels'}catch(error){}

function mapIsVisible(){return $('#overview').classList.contains('active')&&$('#sea-map').clientWidth>0&&$('#sea-map').clientHeight>0}
function rerenderVisibleMap(){if(!state||!mapIsVisible())return;requestAnimationFrame(()=>requestAnimationFrame(()=>renderMap(state.vessels||[],state.config.bounds,state.config)))}
function showPage(id){const page=$(`#${id}`)?id:'overview';$$('.nav').forEach(node=>node.classList.toggle('active',node.dataset.page===page));$$('.page').forEach(node=>node.classList.toggle('active',node.id===page));$('#page-title').textContent={overview:'Overview',fleet:'Live traffic',area:'Watch area'}[page]||'Overview';history.replaceState(null,'',`#${page}`);window.scrollTo({top:0,behavior:'smooth'});if(page==='overview')rerenderVisibleMap()}
$$('.nav').forEach(node=>node.onclick=()=>showPage(node.dataset.page));$$('[data-go]').forEach(node=>node.onclick=()=>showPage(node.dataset.go));

const MAP_TILE=256;
const vesselFlag=mmsi=>window.BaiamonteVesselFlag(mmsi);
function geoPoint(lat,lon,zoom){const scale=MAP_TILE*2**zoom,sine=Math.max(-.9999,Math.min(.9999,Math.sin(lat*Math.PI/180)));return{x:(lon+180)/360*scale,y:(.5-Math.log((1+sine)/(1-sine))/(4*Math.PI))*scale}}
function dashboardView(bounds){const width=$('#sea-map').clientWidth,height=$('#sea-map').clientHeight,centerLat=(bounds.north+bounds.south)/2,centerLon=(bounds.east+bounds.west)/2;let zoom=2;for(let candidate=12;candidate>=2;candidate--){const nw=geoPoint(bounds.north,bounds.west,candidate),se=geoPoint(bounds.south,bounds.east,candidate);if(se.x-nw.x<=width*.86&&se.y-nw.y<=height*.82){zoom=candidate;break}}const center=geoPoint(centerLat,centerLon,zoom);return{zoom,width,height,left:center.x-width/2,top:center.y-height/2}}
function renderDashboardTiles(view,style){const layer=$('#dashboard-tiles'),count=2**view.zoom;layer.innerHTML='';for(let y=Math.max(0,Math.floor(view.top/MAP_TILE));y<=Math.min(count-1,Math.floor((view.top+view.height)/MAP_TILE));y++){for(let x=Math.floor(view.left/MAP_TILE);x<=Math.floor((view.left+view.width)/MAP_TILE);x++){const image=document.createElement('img');image.alt='';image.src=`api/map-tile/${style}/${view.zoom}/${((x%count)+count)%count}/${y}.png`;image.style.left=`${x*MAP_TILE-view.left}px`;image.style.top=`${y*MAP_TILE-view.top}px`;layer.appendChild(image)}}}
async function renderDashboardWeather(view,cfg){const layer=$('#dashboard-weather'),status=$('#dashboard-weather-status');layer.innerHTML='';status.hidden=true;if(!cfg.weather_overlay_dashboard)return;try{const response=await fetch('api/weather-maps',{cache:'no-store'});if(!response.ok)throw new Error(response.status);const metadata=await response.json(),frames=metadata&&metadata.radar&&metadata.radar.past||[],frame=frames[frames.length-1];if(!frame||!frame.path)throw new Error('no radar frame');const zoom=Math.min(view.zoom,7),scale=2**(view.zoom-zoom),size=MAP_TILE*scale,count=2**zoom;for(let y=Math.max(0,Math.floor(view.top/size));y<=Math.min(count-1,Math.floor((view.top+view.height)/size));y++){for(let x=Math.floor(view.left/size);x<=Math.floor((view.left+view.width)/size);x++){const image=document.createElement('img');image.alt='';image.src=`api/weather-tile${frame.path}/256/${zoom}/${((x%count)+count)%count}/${y}/2/1_1.png`;image.style.left=`${x*size-view.left}px`;image.style.top=`${y*size-view.top}px`;image.style.width=`${size}px`;image.style.height=`${size}px`;image.style.opacity=Math.max(.1,Math.min(1,Number(cfg.tv_weather_opacity||65)/100));layer.appendChild(image)}}status.textContent=`LIVE RAIN · ${new Date(frame.time*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}`;status.hidden=false}catch(error){status.textContent='RAIN RADAR UNAVAILABLE';status.hidden=false}}
function mapVesselDetail(v){const info=vesselFlag(v.mmsi);return`<div class="map-detail-identity"><span>${info.flag}</span><div><small>${esc(info.country)} · MMSI ${esc(v.mmsi)}</small><b>${esc(v.name||'Unknown vessel')}</b></div></div><div class="map-detail-metrics"><span><small>SPEED</small><b>${esc(fmt(v.sog))} kn</b></span><span><small>COURSE</small><b>${esc(fmt(v.cog))}°</b></span><span><small>DESTINATION</small><b>${esc(v.destination||'Not broadcast')}</b></span><span><small>LAST SEEN</small><b>${esc(ago(v.last_seen))}</b></span></div>`}
function showMapVessel(v){selectedMapMmsi=String(v.mmsi);const detail=$('#map-vessel-detail');$('#map-detail-content').innerHTML=mapVesselDetail(v);detail.hidden=false;$$('.vessel-marker').forEach(node=>node.classList.toggle('selected',node.dataset.mmsi===selectedMapMmsi))}
function hideMapVessel(){selectedMapMmsi=null;$('#map-vessel-detail').hidden=true;$$('.vessel-marker').forEach(node=>node.classList.remove('selected'))}
function layoutMapLabels(view){const groups={east:[],west:[]};$$('.vessel-marker:not(.label-hidden)').forEach((node,index)=>{const x=Number(node.dataset.mapX),y=Number(node.dataset.mapY),side=x<view.width*.4?'east':x>view.width*.6?'west':index%2?'west':'east';node.classList.remove('label-east','label-west');node.classList.add(`label-${side}`);groups[side].push({node,y})});Object.values(groups).forEach(items=>{items.sort((a,b)=>a.y-b.y);const cardHeight=58,gap=6,total=items.length*cardHeight+Math.max(0,items.length-1)*gap,median=items.length?items[Math.floor(items.length/2)].y:view.height/2,start=Math.max(6,Math.min(view.height-total-6,median-total/2));items.forEach((item,index)=>{const markerTop=item.y-13.5;item.node.querySelector('.map-vessel-label').style.top=`${start+index*(cardHeight+gap)-markerTop}px`})})}
function renderMap(vessels,bounds,cfg){if(!mapIsVisible())return;const world=$('#map-world'),view=dashboardView(bounds);$('#sea-map').dataset.display=mapDisplayMode;renderDashboardTiles(view,cfg.map_style||'standard');renderDashboardWeather(view,cfg);$$('.vessel-marker').forEach(node=>node.remove());let positioned=0,selected=null;vessels.forEach(v=>{if(typeof v.latitude!=='number'||typeof v.longitude!=='number')return;const point=geoPoint(v.latitude,v.longitude,view.zoom),x=point.x-view.left,y=point.y-view.top;if(x<0||y<0||x>view.width||y>view.height)return;positioned+=1;const info=vesselFlag(v.mmsi),node=document.createElement('button');node.type='button';node.className='vessel-marker label-east';if(positioned>10)node.classList.add('label-hidden');node.dataset.mmsi=String(v.mmsi);node.dataset.mapX=String(x);node.dataset.mapY=String(y);node.style.left=`${x}px`;node.style.top=`${y}px`;node.title=`${info.country} · ${v.name||'Unknown vessel'} · ${fmt(v.sog)} kn`;node.innerHTML=`<span class="vessel-arrow" style="transform:rotate(${Number(v.heading??v.cog)||0}deg)">▲</span><span class="map-vessel-label"><b>${info.flag} ${esc(v.name||'Unknown vessel')}</b><span>MMSI ${esc(v.mmsi)} · ${esc(v.vessel_type||v.vessel_class||'AIS contact')}</span><span>${esc(fmt(v.sog))} kn · ${esc(v.destination||'Destination not broadcast')}</span><em>${esc(ago(v.last_seen))}</em></span>`;node.onclick=event=>{event.stopPropagation();showMapVessel(v)};if(String(v.mmsi)===selectedMapMmsi){node.classList.add('selected');selected=v}world.appendChild(node)});if(mapDisplayMode==='labels')layoutMapLabels(view);if(mapDisplayMode==='focus'&&selected)showMapVessel(selected);else if(mapDisplayMode==='labels')$('#map-vessel-detail').hidden=true;else if(selectedMapMmsi&&!selected)hideMapVessel();$('#map-empty').classList.toggle('hidden',positioned>0)}
function vesselRow(v){const info=vesselFlag(v.mmsi);return`<div class="recent-row"><span class="recent-flag" title="${esc(info.country)}">${info.flag}</span><span><b>${esc(v.name||'Unknown vessel')}</b><small>${esc(info.country)} · MMSI ${esc(v.mmsi)} · ${esc(v.nav_status_string||'Status unavailable')}</small></span><em>${esc(fmt(v.sog))} kn</em></div>`}
function vesselCard(v){const info=vesselFlag(v.mmsi),mark=(v.call_sign||v.imo_number||'AIS').toString().slice(0,8);return`<article class="vessel-card"><div class="vessel-card-top"><div class="identity-mark"><span class="identity-flag" title="${esc(info.country)}"><small>FLAG</small><span class="card-flag">${info.flag}</span></span><span class="operator-mark">${esc(mark)}</span></div><span class="status">${esc(v.nav_status_string||'Not defined')}</span></div><h3>${esc(v.name||'Unknown vessel')}</h3><span class="mmsi">${esc(info.country)} · MID ${esc(info.mid||'—')} · MMSI ${esc(v.mmsi)} · ${esc(v.vessel_type||v.vessel_class||'Unknown type')}</span><div class="vessel-metrics"><div><small>SPEED</small><b>${esc(fmt(v.sog))} kn</b></div><div><small>COURSE</small><b>${esc(fmt(v.cog))}°</b></div><div><small>HEADING</small><b>${esc(fmt(v.heading))}°</b></div><div><small>SEEN</small><b>${esc(ago(v.last_seen))}</b></div></div><div class="destination"><span>Destination</span><b>${esc(v.destination||'Not broadcast')}</b></div></article>`}

function render(data){
  state=data;
  const vessels=data.vessels||[],cfg=data.config,bounds=cfg.bounds,connected=data.connection==='Connected',feed=data.feed||{},decoder=data.decoder||{},operational=connected||feed.received>0||decoder.state==='Running';
  $('#side-light').classList.toggle('online',operational);
  $('#hero-light').classList.toggle('online',operational);
  $('#side-status').textContent=decoder.state==='Running'?'Local AIS-catcher online':connected?'AISHub connected':data.connection;
  $('#hero-status').textContent=decoder.state==='Running'?'Local dual-channel AIS receiver is online':connected?'Reciprocal AIS network is online':`AISHub ${String(data.connection).toLowerCase()}`;
  $('#receiver-status').textContent=data.connection;
  $('#vessel-count').textContent=`${vessels.length} active`;
  $('#nav-count').textContent=vessels.length;
  $('#fleet-badge').textContent=`${vessels.length} active`;
  $('#watch-mode').textContent=cfg.watchlist_count?`${cfg.watchlist_count} priority MMSI`:'All vessels';
  $('#map-mode').textContent=cfg.map_entities?'Enabled':'Overview only';
  $('#link-card').textContent=feed.state||data.connection;
  $('#link-detail').textContent=feed.receiver_address?`${feed.receiver_name} · ${feed.received} messages received`:(data.last_error||'Waiting for AIS hardware').slice(0,70);
  $('#watchlist-count').textContent=cfg.watchlist_count?`${cfg.watchlist_count} priority vessels`:'Open watch';
  $('#last-check').textContent=`Updated ${new Date(data.generated_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}`;
  const latest=vessels[0];
  $('#latest-name').textContent=latest?.name||'No contacts yet';
  $('#latest-detail').textContent=latest?`${fmt(latest.sog)} kn · ${ago(latest.last_seen)}`:'The watch area is clear';
  $('#bounds-label').textContent=`${bounds.south.toFixed(3)}, ${bounds.west.toFixed(3)} → ${bounds.north.toFixed(3)}, ${bounds.east.toFixed(3)}`;
  renderMap(vessels,bounds,cfg);
  $('#recent-vessels').innerHTML=vessels.length?vessels.slice(0,4).map(vesselRow).join(''):'<div class="empty">No vessel broadcasts received in this watch area yet.</div>';
  $('#fleet-grid').innerHTML=vessels.length?vessels.map(vesselCard).join(''):'<article class="panel empty">AISHub is scanning. Vessels will appear here automatically.</article>';
  const events=data.events||[];
  $('#events').innerHTML=events.length?events.slice(0,5).map(event=>`<div class="event-row"><div class="vessel-icon"><svg><use href="#i-radar"/></svg></div><span><b>${esc(event.message)}</b><small>${esc(ago(event.time))}</small></span></div>`).join(''):'<div class="empty">The operations journal is ready for the first contact.</div>';
  $('#north').textContent=bounds.north.toFixed(6);$('#south').textContent=bounds.south.toFixed(6);$('#east').textContent=bounds.east.toFixed(6);$('#west').textContent=bounds.west.toFixed(6);
  $('#class-b').textContent=cfg.include_class_b?'Included':'Excluded';
  $('#timeout').textContent=`${cfg.timeout_minutes} minutes`;
  $('#entities').textContent=cfg.map_entities?'Enabled':'Disabled';
  $('#decoder-title').textContent=decoder.enabled?`AIS-catcher ${decoder.version||''}`.trim():'External decoded receiver';
  $('#decoder-status').textContent=decoder.state||'Not enabled';
  $('#decoder-device').textContent=decoder.enabled?`RTL-SDR ${fmt(decoder.device)}`:String(cfg.receiver_mode||'external').toUpperCase();
  $('#decoder-tuning').textContent=decoder.enabled?`${fmt(decoder.gain)} dB · ${fmt(decoder.ppm)} PPM`:'Handled by receiver';
  $('#decoder-bandwidth').textContent=decoder.enabled?`${fmt(decoder.bandwidth)} · AGC ${decoder.rtl_agc?'on':'off'} · bias ${decoder.bias_tee?'on':'off'}`:'NMEA input';
  const logEntries=data.receiver_log||[];
  $('#receiver-log').innerHTML=logEntries.length?logEntries.slice(0,30).map(item=>`<div class="receiver-log-row"><time>${esc(new Date(item.time).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}))}</time><span>${esc(item.message)}</span></div>`).join(''):'<div class="empty">Receiver activity will appear here.</div>';
  const airport=data.flightaware_weather||{};$('#airport-weather-title').textContent=airport.airport?`${airport.airport} airport observation`:'Airport observation';$('#airport-weather').textContent=!airport.enabled?'Disabled in app configuration':airport.error?airport.error:[airport.weather,airport.temperature_c!==null&&airport.temperature_c!==undefined?`${airport.temperature_c}°C`:'',airport.wind_speed_kts!==null&&airport.wind_speed_kts!==undefined?`Wind ${airport.wind_speed_kts} kt`:'',airport.visibility_miles!==null&&airport.visibility_miles!==undefined?`Visibility ${airport.visibility_miles} mi`:''].filter(Boolean).join(' · ')||'Waiting for FlightAware observation';
}

const overviewMap=$('#sea-map'),overviewWorld=$('#map-world');
const overviewView={x:0,y:0,scale:1};
let mapDragging=false,mapPointerX=0,mapPointerY=0,mapPinchDistance=0;
const mapPointers=new Map();
function applyOverviewView(){
  const limitX=overviewMap.clientWidth*.75,limitY=overviewMap.clientHeight*.75;
  overviewView.x=Math.max(-limitX,Math.min(limitX,overviewView.x));
  overviewView.y=Math.max(-limitY,Math.min(limitY,overviewView.y));
  overviewWorld.style.transform=`translate(${overviewView.x}px,${overviewView.y}px) scale(${overviewView.scale})`;
}
function changeOverviewZoom(amount){overviewView.scale=Math.max(.7,Math.min(3,overviewView.scale+amount));applyOverviewView()}
function changeOverviewHeight(amount){const height=Math.max(260,Math.min(720,overviewMap.getBoundingClientRect().height+amount));overviewMap.style.height=`${Math.round(height)}px`;try{localStorage.setItem('baiamonteOverviewMapHeight',String(Math.round(height)))}catch(error){}applyOverviewView();rerenderVisibleMap()}
function resetOverviewMap(){overviewView.x=0;overviewView.y=0;overviewView.scale=1;applyOverviewView()}
overviewMap.addEventListener('pointerdown',event=>{
  if(event.target.closest('.map-controls,.vessel-marker,.map-vessel-detail'))return;
  const bounds=overviewMap.getBoundingClientRect();
  if(event.clientX>bounds.right-30&&event.clientY>bounds.bottom-30)return;
  mapPointers.set(event.pointerId,{x:event.clientX,y:event.clientY});mapDragging=true;mapPointerX=event.clientX;mapPointerY=event.clientY;overviewMap.classList.add('dragging');overviewMap.setPointerCapture(event.pointerId);if(mapPointers.size===2){const points=[...mapPointers.values()];mapPinchDistance=Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y)}
});
overviewMap.addEventListener('pointermove',event=>{if(!mapPointers.has(event.pointerId))return;mapPointers.set(event.pointerId,{x:event.clientX,y:event.clientY});if(mapPointers.size===2){const points=[...mapPointers.values()],distance=Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y);if(mapPinchDistance)changeOverviewZoom((distance-mapPinchDistance)/220);mapPinchDistance=distance;return}if(!mapDragging)return;overviewView.x+=event.clientX-mapPointerX;overviewView.y+=event.clientY-mapPointerY;mapPointerX=event.clientX;mapPointerY=event.clientY;applyOverviewView()});
function stopMapPointer(event){mapPointers.delete(event.pointerId);mapPinchDistance=0;mapDragging=mapPointers.size>0;overviewMap.classList.toggle('dragging',mapDragging);if(overviewMap.hasPointerCapture(event.pointerId))overviewMap.releasePointerCapture(event.pointerId)}
overviewMap.addEventListener('pointerup',stopMapPointer);
overviewMap.addEventListener('pointercancel',stopMapPointer);
overviewMap.addEventListener('wheel',event=>{event.preventDefault();changeOverviewZoom(event.deltaY<0?.15:-.15)},{passive:false});
$('#map-zoom-in').onclick=()=>changeOverviewZoom(.2);
$('#map-zoom-out').onclick=()=>changeOverviewZoom(-.2);
$('#map-size-up').onclick=()=>changeOverviewHeight(60);
$('#map-size-down').onclick=()=>changeOverviewHeight(-60);
$('#map-reset').onclick=resetOverviewMap;
function setMapDisplay(mode){mapDisplayMode=mode==='focus'?'focus':'labels';try{localStorage.setItem('baiamonteAisMapDisplay',mapDisplayMode)}catch(error){}$$('[data-map-display]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mapDisplay===mapDisplayMode)));$('#map-mode-hint').textContent=mapDisplayMode==='labels'?'All nearby vessels labeled · drag or pinch to explore':'Tap a vessel to open its full details';if(mapDisplayMode==='labels')hideMapVessel();rerenderVisibleMap()}
$$('[data-map-display]').forEach(button=>button.onclick=()=>setMapDisplay(button.dataset.mapDisplay));
$('#map-detail-close').onclick=event=>{event.stopPropagation();hideMapVessel()};
setMapDisplay(mapDisplayMode);
try{
  const savedHeight=Number(localStorage.getItem('baiamonteOverviewMapHeight'));
  if(savedHeight>=260&&savedHeight<=720)overviewMap.style.height=`${savedHeight}px`;
  new ResizeObserver(entries=>{const height=Math.round(entries[0].contentRect.height);if(height>=260&&height<=720)localStorage.setItem('baiamonteOverviewMapHeight',String(height));applyOverviewView();rerenderVisibleMap()}).observe(overviewMap);
}catch(error){console.debug('Map size preference is unavailable',error)}
async function refresh(){if(refreshRunning)return;refreshRunning=true;const button=$('#refresh');button.disabled=true;try{const response=await fetch('api/status',{cache:'no-store'});if(!response.ok)throw new Error(`Dashboard API ${response.status}`);const data=await response.json();refreshFailures=0;render(data)}catch(error){refreshFailures+=1;$('#last-check').textContent=state?'Update delayed · retrying':'Connecting to dashboard…';if(!state&&refreshFailures>=3){$('#receiver-status').textContent='Dashboard unavailable';$('#hero-status').textContent='Waiting for the Baiamonte AIS service'}console.error(error)}finally{refreshRunning=false;button.disabled=false}}
$('#refresh').onclick=refresh;showPage(location.hash.slice(1)||'overview');refresh();setInterval(()=>{if(!document.hidden)refresh()},10000);document.addEventListener('visibilitychange',()=>{if(!document.hidden){rerenderVisibleMap();refresh()}});addEventListener('resize',rerenderVisibleMap);
