const map=document.querySelector('#map');
const tiles=document.querySelector('#tiles');
const weather=document.querySelector('#weather');
const boats=document.querySelector('#boats');
const empty=document.querySelector('#empty');
const vesselList=document.querySelector('#vessel-list');
const fleetCount=document.querySelector('#fleet-count');
const feedStatus=document.querySelector('#feed-status');
const feedLight=document.querySelector('#feed-light');
const weatherCredit=document.querySelector('#weather-credit');
const weatherTime=document.querySelector('#weather-time');
const TILE=256;
let latest=null;
let weatherMetadata=null;
let weatherMetadataFetched=0;
let manualCenter=null,manualZoom=null;
const tvParams=new URLSearchParams(location.search);
const requestedArea=(tvParams.get('area')||'').toLowerCase();
const requestedZoomDelta=clampedParam(tvParams.get('map_zoom'),-6,20,0);
const requestedTargetScale=clampedParam(tvParams.get('target_size'),50,180,100)/100;
window.BaiamonteNativeMapControls=true;
let activeArea=requestedArea;
let tvVesselsVisible=null;
const tvPointers={};
let tvDrag=null,tvPinch=0;
let renderedTiles={};
let renderedWeatherTiles={};
let weatherRenderToken=0;
let tvAreaSwitchKey='';
let mapFrame=0;
let tvRefreshRunning=false,tvRefreshQueued=false;

const apiPath=location.pathname.replace(/\/tv\/?$/,'')+'/api/status';
const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));
function clampedParam(value,min,max,fallback){const parsed=Number(value);return Number.isFinite(parsed)?Math.min(max,Math.max(min,parsed)):fallback}
const escapeHtml=value=>String(value===null||value===undefined?'':value).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const flagInfo=mmsi=>window.BaiamonteVesselFlag(mmsi);
function tvAreas(config){const fallback=[{id:'baiamonte',name:'Baiamonte Sicily',bounds:config.bounds,enabled:true}],areas=(config.map_areas||fallback).filter(function(area){return area.enabled!==false});return areas.length?areas:fallback}
function currentTvArea(config){const areas=tvAreas(config),preferred=requestedArea||config.tv_default_map_area||'baiamonte';return areas.find(function(area){return area.id===activeArea})||areas.find(function(area){return area.id===preferred})||areas.find(function(area){return area.id==='baiamonte'})||areas[0]}
function currentTvBounds(){return currentTvArea(latest.config).bounds}
function chooseTvArea(areaId){activeArea=areaId;manualCenter=null;manualZoom=null;const url=new URL(location.href);url.searchParams.set('area',areaId);history.replaceState(null,'',url.pathname+url.search);if(latest)render(latest)}
function renderTvAreaSwitch(config){const area=currentTvArea(config),switcher=document.querySelector('#tv-area-switch'),key=tvAreas(config).map(function(item){return `${item.id}:${item.name}:${item.id===area.id}`}).join('|');activeArea=area.id;if(key!==tvAreaSwitchKey){tvAreaSwitchKey=key;switcher.innerHTML=tvAreas(config).map(function(item){return `<button type="button" data-area="${escapeHtml(item.id)}" aria-pressed="${item.id===area.id}">${escapeHtml(item.name)}</button>`}).join('');Array.prototype.forEach.call(switcher.querySelectorAll('[data-area]'),function(button){button.onclick=function(){chooseTvArea(button.getAttribute('data-area'))}})}return area}

function tvHomeCenter(config,area){
  const reference=config.reference_location||{};
  if(area.id==='baiamonte'&&Number.isFinite(Number(reference.latitude))&&Number.isFinite(Number(reference.longitude))){
    return{lat:Number(reference.latitude),lon:Number(reference.longitude)};
  }
  return{lat:(area.bounds.north+area.bounds.south)/2,lon:(area.bounds.east+area.bounds.west)/2};
}

function currentTvView(){const area=currentTvArea(latest.config);return fitView(area.bounds,map.clientWidth,map.clientHeight,tvHomeCenter(latest.config,area))}

function project(lat,lon,zoom){
  const scale=TILE*Math.pow(2,zoom);
  const sin=clamp(Math.sin(lat*Math.PI/180),-.9999,.9999);
  return {
    x:(lon+180)/360*scale,
    y:(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*scale
  };
}

function fitView(bounds,width,height,homeCenter){
  const centerLat=(bounds.north+bounds.south)/2;
  const centerLon=(bounds.east+bounds.west)/2;
  let zoom=2;
  for(let candidate=18;candidate>=2;candidate--){
    const nw=project(bounds.north,bounds.west,candidate);
    const se=project(bounds.south,bounds.east,candidate);
    if(se.x-nw.x<=width*.82&&se.y-nw.y<=height*.78){zoom=candidate;break}
  }
  zoom=manualZoom===null?clamp(zoom+requestedZoomDelta,2,18):manualZoom;
  const chosen=manualCenter||homeCenter||{lat:centerLat,lon:centerLon};
  const center=project(chosen.lat,chosen.lon,zoom);
  return {zoom,center:chosen,originX:center.x-width/2,originY:center.y-height/2};
}

function renderTiles(view,width,height,style){
  const nextTiles={};
  const count=Math.pow(2,view.zoom);
  const firstX=Math.floor(view.originX/TILE);
  const lastX=Math.floor((view.originX+width)/TILE);
  const firstY=Math.max(0,Math.floor(view.originY/TILE));
  const lastY=Math.min(count-1,Math.floor((view.originY+height)/TILE));
  for(let y=firstY;y<=lastY;y++){
    for(let x=firstX;x<=lastX;x++){
      const wrappedX=((x%count)+count)%count,key=`${style}/${view.zoom}/${wrappedX}/${y}`;
      const image=renderedTiles[key]||document.createElement('img');
      if(!renderedTiles[key]){image.className='tile';image.alt='';image.decoding='async';image.src=`api/map-tile/${key}.png`;tiles.appendChild(image)}
      image.style.left=`${x*TILE-view.originX}px`;
      image.style.top=`${y*TILE-view.originY}px`;
      nextTiles[key]=image;
    }
  }
  Object.keys(renderedTiles).forEach(function(key){if(!nextTiles[key]&&renderedTiles[key].parentNode)renderedTiles[key].parentNode.removeChild(renderedTiles[key])});
  renderedTiles=nextTiles;
}

function getWeatherFrame(){
  if(weatherMetadata&&Date.now()-weatherMetadataFetched<300000)return Promise.resolve(weatherMetadata);
  return fetch('api/weather-maps',{cache:'no-store'}).then(function(response){
    if(!response.ok)throw new Error(`RainViewer metadata ${response.status}`);
    return response.json();
  }).then(function(metadata){
    const frames=metadata&&metadata.radar&&metadata.radar.past||[];
    if(!metadata.host||!frames.length)throw new Error('RainViewer has no current radar frame');
    weatherMetadata={host:metadata.host,frame:frames[frames.length-1]};
    weatherMetadataFetched=Date.now();
    return weatherMetadata;
  });
}

function renderWeather(view,width,height,config){
  const token=++weatherRenderToken;
  weatherCredit.hidden=true;
  if(!config.tv_weather_overlay){Object.keys(renderedWeatherTiles).forEach(function(key){const tile=renderedWeatherTiles[key];if(tile.parentNode)tile.parentNode.removeChild(tile)});renderedWeatherTiles={};return}
  getWeatherFrame().then(function(metadata){
    if(token!==weatherRenderToken)return;
    const nextTiles={};
    const overlayZoom=Math.min(view.zoom,7);
    const scale=Math.pow(2,view.zoom-overlayZoom);
    const renderedTile=TILE*scale;
    const count=Math.pow(2,overlayZoom);
    const firstX=Math.floor(view.originX/renderedTile);
    const lastX=Math.floor((view.originX+width)/renderedTile);
    const firstY=Math.max(0,Math.floor(view.originY/renderedTile));
    const lastY=Math.min(count-1,Math.floor((view.originY+height)/renderedTile));
    const opacity=clamp(Number(config.tv_weather_opacity||65),10,100)/100;
    for(let y=firstY;y<=lastY;y++){
      for(let x=firstX;x<=lastX;x++){
        const wrappedX=((x%count)+count)%count,key=`${metadata.frame.path}/${overlayZoom}/${wrappedX}/${y}`;
        const tile=renderedWeatherTiles[key]||document.createElement('img');
        if(!renderedWeatherTiles[key]){tile.className='weather-tile';tile.alt='';tile.decoding='async';tile.src=`api/weather-tile${metadata.frame.path}/256/${overlayZoom}/${wrappedX}/${y}/2/1_1.png`;weather.appendChild(tile)}
        tile.style.left=`${x*renderedTile-view.originX}px`;
        tile.style.top=`${y*renderedTile-view.originY}px`;
        tile.style.width=`${renderedTile}px`;
        tile.style.height=`${renderedTile}px`;
        tile.style.opacity=opacity;
        nextTiles[key]=tile;
      }
    }
    Object.keys(renderedWeatherTiles).forEach(function(key){const tile=renderedWeatherTiles[key];if(!nextTiles[key]&&tile.parentNode)tile.parentNode.removeChild(tile)});
    renderedWeatherTiles=nextTiles;
    weatherTime.textContent=`Radar ${new Date(metadata.frame.time*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`;
    weatherCredit.hidden=false;
  }).catch(function(error){
    if(token!==weatherRenderToken)return;
    weatherTime.textContent='Radar temporarily unavailable';
    weatherCredit.hidden=false;
    console.warn(error);
  });
}

function vesselIcon(vessel){
  const type=String(vessel.vessel_type||vessel.vessel_class||'').toLowerCase();
  if(type.includes('aid to navigation')||type.includes('base station'))return{kind:'aton',svg:'<circle class="icon-outline" cx="20" cy="28" r="14"/><path class="icon-detail" d="M20 8 29 28 20 48 11 28Z"/><circle class="icon-light" cx="20" cy="28" r="4"/>'};
  if(type.includes('sail')||type.includes('pleasure'))return{kind:'sail',svg:'<path class="icon-outline" d="M20 2 34 43 20 54 7 43Z"/><path class="icon-detail" d="M19 9 19 39 8 39Z"/><path class="icon-light" d="M21 13 31 39 21 39Z"/>'};
  if(type.includes('tanker'))return{kind:'tanker',svg:'<path class="icon-outline" d="M20 2 33 14 31 48 20 55 9 48 7 14Z"/><circle class="icon-detail" cx="14" cy="27" r="4"/><circle class="icon-detail" cx="26" cy="27" r="4"/><path class="icon-light" d="M12 39h16v8H12z"/>'};
  if(type.includes('cargo'))return{kind:'cargo',svg:'<path class="icon-outline" d="M20 2 34 15 32 49 20 55 8 49 6 15Z"/><path class="icon-detail" d="M10 17h20v9H10zm0 12h20v9H10z"/><path class="icon-light" d="M12 41h16v7H12z"/>'};
  if(type.includes('passenger')||type.includes('ferry'))return{kind:'passenger',svg:'<path class="icon-outline" d="M20 1 33 13 31 49 20 55 9 49 7 13Z"/><path class="icon-light" d="M11 17h18v22H11z"/><path class="icon-windows" d="M14 20h3v3h-3zm6 0h3v3h-3zm6 0h3v3h-3zm-12 7h3v3h-3zm6 0h3v3h-3zm6 0h3v3h-3z"/>'};
  if(type.includes('tug'))return{kind:'tug',svg:'<path class="icon-outline" d="M20 4 31 13 34 40 27 53H13L6 40l3-27Z"/><path class="icon-light" d="M12 16h16v20H12z"/><path class="icon-detail" d="M15 10h10v8H15z"/>'};
  if(type.includes('fishing'))return{kind:'fishing',svg:'<path class="icon-outline" d="M20 2 30 14 29 50 20 55 11 50 10 14Z"/><path class="icon-detail" d="M13 18h14v21H13zM5 21l8 8M35 21l-8 8"/><path class="icon-light" d="M16 10h8v9h-8z"/>'};
  if(type.includes('search and rescue')||type.includes('law enforcement')||type.includes('military'))return{kind:'rescue',svg:'<path class="icon-outline" d="M20 1 33 15 31 48 20 55 9 48 7 15Z"/><path class="icon-rescue" d="M17 16h6v8h8v6h-8v8h-6v-8H9v-6h8Z"/>'};
  return{kind:'vessel',svg:'<path class="icon-outline" d="M20 1 34 16 31 48 20 55 9 48 6 16Z"/><path class="icon-light" d="M12 18h16v22H12z"/><path class="icon-detail" d="M15 11h10v9H15z"/>'};
}

function boatNode(vessel,view){
  const point=project(Number(vessel.latitude),Number(vessel.longitude),view.zoom);
  const node=document.createElement('div');
  node.className='boat';
  const x=point.x-view.originX,y=point.y-view.originY;
  node.style.left=`${x}px`;
  node.style.top=`${y}px`;
  node.setAttribute('data-map-x',String(x));
  node.setAttribute('data-map-y',String(y));
  const direction=Number(vessel.heading!==null&&vessel.heading!==undefined?vessel.heading:(vessel.cog!==null&&vessel.cog!==undefined?vessel.cog:0));
  const info=flagInfo(vessel.mmsi);
  const icon=vesselIcon(vessel);
  const safeName=escapeHtml(`${info.flag} ${vessel.name||`MMSI ${vessel.mmsi}`}`);
  node.classList.add(`boat-${icon.kind}`);
  node.style.setProperty('--target-scale',String(requestedTargetScale));
  node.innerHTML=`<svg class="boat-icon" viewBox="0 0 40 56" style="--heading:${direction}deg" aria-hidden="true">${icon.svg}</svg><span class="boat-label">${safeName}</span>`;
  return node;
}

function vesselIsOnScreen(vessel,view,width,height){
  const point=project(Number(vessel.latitude),Number(vessel.longitude),view.zoom);
  const x=point.x-view.originX,y=point.y-view.originY,margin=28*requestedTargetScale;
  return x>=-margin&&x<=width+margin&&y>=-margin&&y<=height+margin;
}

function layoutTvLabels(nodes,width,height){
  const occupied=[],labelWidth=width>=1500?180:width<800?128:158,labelHeight=24,margin=8,maxLabels=width<800?6:12;
  Array.prototype.forEach.call(nodes,function(node,index){
    const label=node.querySelector('.boat-label'),x=Number(node.getAttribute('data-map-x')),y=Number(node.getAttribute('data-map-y'));
    if(index>=maxLabels){node.classList.add('label-hidden');return}
    const candidates=[{x:x+18,y:y-labelHeight/2},{x:x-labelWidth-18,y:y-labelHeight/2},{x:x-labelWidth/2,y:y+22},{x:x-labelWidth/2,y:y-labelHeight-22}],fits=function(rect){if(rect.x<margin||rect.y<margin||rect.x+rect.w>width-margin||rect.y+rect.h>height-margin)return false;return !occupied.some(function(other){return rect.x<other.x+other.w+6&&rect.x+rect.w+6>other.x&&rect.y<other.y+other.h+6&&rect.y+rect.h+6>other.y})},chosen=candidates.map(function(item){return{x:item.x,y:item.y,w:labelWidth,h:labelHeight}}).find(fits);
    if(!chosen){node.classList.add('label-hidden');return}
    occupied.push(chosen);label.style.left=`${chosen.x-x}px`;label.style.top=`${chosen.y-y}px`;label.style.width=`${labelWidth}px`;label.style.maxWidth=`${labelWidth}px`;label.style.transform='none'
  })
}

function vesselRow(vessel){
  const info=flagInfo(vessel.mmsi);
  const speed=Number.isFinite(Number(vessel.sog))?`${Number(vessel.sog).toFixed(1)} kn`:'—';
  const direction=Number.isFinite(Number(vessel.heading))?`${Math.round(Number(vessel.heading))}°`:Number.isFinite(Number(vessel.cog))?`${Math.round(Number(vessel.cog))}°`:'—';
  const distance=Number.isFinite(Number(vessel.distance_km))?`${Number(vessel.distance_km).toFixed(1)} km`:'Distance unavailable';
  const areaStatus=vessel.area_status==='inbound'?'Inbound':vessel.area_status==='in_area'?'In area':'Nearby';
  return `<article class="vessel-row"><span class="flag" title="${escapeHtml(info.country)}">${info.flag}</span><div class="vessel-main"><div class="vessel-title"><h2>${escapeHtml(vessel.name||'Unknown vessel')}</h2><strong>${speed}</strong></div><div class="vessel-meta"><span>${escapeHtml(areaStatus)}</span><span>${escapeHtml(info.country)}</span><span>${distance}</span><span>${direction}</span></div><div class="vessel-dest"><span>${escapeHtml(vessel.destination||vessel.nav_status_string||'No destination broadcast')}</span><em>${escapeHtml(vessel.vessel_type||vessel.station||'AIS contact')}</em></div></div></article>`;
}

function render(data){
  latest=data;
  const width=map.clientWidth,height=map.clientHeight;
  if(width<64||height<64)return;
  const cfg=data.config;
  if(tvVesselsVisible===null)tvVesselsVisible=cfg.tv_map_vessels!==false;
  document.querySelector('#tv-vessels-toggle').setAttribute('aria-pressed',String(tvVesselsVisible));
  const area=renderTvAreaSwitch(cfg);
  const bounds=area.bounds;
  const view=fitView(bounds,width,height,tvHomeCenter(cfg,area));
  renderTiles(view,width,height,cfg.map_style||'standard');
  renderWeather(view,width,height,cfg);
  boats.innerHTML='';
  const areaVessels=(data.vessels||[]).filter(v=>String(v.area_id||'baiamonte')===area.id&&Number.isFinite(Number(v.latitude))&&Number.isFinite(Number(v.longitude)));
  const freshCutoff=Date.now()-Math.max(1,Number(cfg.timeout_minutes)||30)*60000;
  const freshVessels=areaVessels.filter(function(v){const seen=Date.parse(v.last_seen||'');return Number.isFinite(seen)&&seen>=freshCutoff});
  const visible=freshVessels.filter(v=>vesselIsOnScreen(v,view,width,height));
  if(tvVesselsVisible){const mapped=visible.slice().sort(function(a,b){const rank={in_area:0,inbound:1,nearby:2,unknown:3};return (rank[a.area_status]===undefined?3:rank[a.area_status])-(rank[b.area_status]===undefined?3:rank[b.area_status])||Number(a.distance_km||99999)-Number(b.distance_km||99999)});mapped.forEach(vessel=>boats.appendChild(boatNode(vessel,view)));layoutTvLabels(boats.querySelectorAll('.boat'),width,height)}
  empty.classList.toggle('show',tvVesselsVisible&&visible.length===0);
  const liveTraffic=visible.slice().sort(function(a,b){return Number(a.distance_km||99999)-Number(b.distance_km||99999)});
  fleetCount.textContent=liveTraffic.length;
  const previousListScroll=vesselList.scrollTop;
  vesselList.innerHTML=liveTraffic.length?liveTraffic.map(vesselRow).join(''):'<div class="list-empty">No live vessels inside this map view</div>';
  vesselList.scrollTop=Math.min(previousListScroll,Math.max(0,vesselList.scrollHeight-vesselList.clientHeight));
  const proxy=data.rahamin_proxy||{},proxyArea=(proxy.areas||{})[area.id]||{},proxyConnected=proxyArea.state==='Connected'||(area.id==='miami'&&proxy.state==='Connected'&&!proxy.areas),connected=data.connection==='Connected'||proxyConnected;
  feedLight.classList.toggle('online',connected);
  feedStatus.textContent=connected?`${area.name} live · ${liveTraffic.length} in view · stale after ${Math.max(1,Number(cfg.timeout_minutes)||30)} min`:`${area.name} · ${String(proxyArea.state||data.connection).toLowerCase()}`;
}

function refresh(){
  if(tvRefreshRunning){tvRefreshQueued=true;return}
  tvRefreshRunning=true;
  fetch(apiPath,{cache:'no-store'}).then(function(response){
    if(!response.ok)throw new Error(`TV feed ${response.status}`);
    return response.json();
  }).then(render).catch(function(error){
    empty.textContent='Waiting for AIS feed';
    empty.classList.add('show');
    console.error(error);
  }).then(function(){
    tvRefreshRunning=false;
    if(tvRefreshQueued){tvRefreshQueued=false;refresh()}
  });
}

let resizeTimer;
addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>latest&&render(latest),150)});
function rerenderMap(){if(!latest||mapFrame)return;const schedule=window.requestAnimationFrame||function(callback){return setTimeout(callback,16)};mapFrame=schedule(function(){mapFrame=0;render(latest)})}
function resetTvMap(){if(!latest)return;clearTvInteraction();const area=currentTvArea(latest.config);manualCenter=tvHomeCenter(latest.config,area);manualZoom=null;if(mapFrame){const cancel=window.cancelAnimationFrame||clearTimeout;cancel(mapFrame);mapFrame=0}render(latest)}
document.querySelector('#tv-map-in').onclick=function(){if(!latest)return;manualZoom=Math.min(13,(manualZoom===null?currentTvView().zoom:manualZoom)+1);rerenderMap()};
document.querySelector('#tv-map-out').onclick=function(){if(!latest)return;manualZoom=Math.max(2,(manualZoom===null?currentTvView().zoom:manualZoom)-1);rerenderMap()};
document.querySelector('#tv-map-reset').onclick=function(event){event.stopPropagation();resetTvMap()};
document.querySelector('#tv-vessels-toggle').onclick=function(){tvVesselsVisible=!tvVesselsVisible;rerenderMap()};
vesselList.addEventListener('keydown',function(event){const page=Math.max(56,Math.floor(vesselList.clientHeight*.8));if(event.key==='ArrowDown')vesselList.scrollTop+=56;else if(event.key==='ArrowUp')vesselList.scrollTop-=56;else if(event.key==='PageDown')vesselList.scrollTop+=page;else if(event.key==='PageUp')vesselList.scrollTop-=page;else if(event.key==='Home')vesselList.scrollTop=0;else if(event.key==='End')vesselList.scrollTop=vesselList.scrollHeight;else return;event.preventDefault()});
function beginTvDrag(x,y){const view=currentTvView();tvDrag={x:x,y:y,center:view.center,zoom:view.zoom,world:project(view.center.lat,view.center.lon,view.zoom)}}
function moveTvDrag(x,y){if(!tvDrag)return;const scale=TILE*Math.pow(2,tvDrag.zoom),worldX=tvDrag.world.x-(x-tvDrag.x),worldY=tvDrag.world.y-(y-tvDrag.y),lon=worldX/scale*360-180,mercator=Math.PI*(1-2*worldY/scale),lat=Math.atan(Math.sinh(mercator))*180/Math.PI;manualCenter={lat:lat,lon:lon};manualZoom=tvDrag.zoom;rerenderMap()}
function changeTvPinch(distance){if(tvPinch&&Math.abs(distance-tvPinch)>35){manualZoom=Math.max(2,Math.min(13,(manualZoom===null?currentTvView().zoom:manualZoom)+(distance>tvPinch?1:-1)));tvPinch=distance;rerenderMap()}}
function clearTvInteraction(){Object.keys(tvPointers).forEach(function(key){delete tvPointers[key]});tvDrag=null;tvPinch=0}
if('PointerEvent' in window){
  map.addEventListener('pointerdown',function(event){if(!latest||(event.target.closest&&event.target.closest('.tv-map-controls,.tv-area-switch,a')))return;tvPointers[event.pointerId]={x:event.clientX,y:event.clientY};if(map.setPointerCapture)map.setPointerCapture(event.pointerId);if(Object.keys(tvPointers).length===1)beginTvDrag(event.clientX,event.clientY);else{const points=Object.keys(tvPointers).map(function(key){return tvPointers[key]});tvPinch=Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y)}});
  map.addEventListener('pointermove',function(event){if(!tvPointers[event.pointerId]||!latest)return;tvPointers[event.pointerId]={x:event.clientX,y:event.clientY};const points=Object.keys(tvPointers).map(function(key){return tvPointers[key]});if(points.length===2){changeTvPinch(Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y));return}moveTvDrag(event.clientX,event.clientY)});
  function stopTvPointer(event){delete tvPointers[event.pointerId];tvDrag=null;tvPinch=0;if(map.hasPointerCapture&&map.hasPointerCapture(event.pointerId))map.releasePointerCapture(event.pointerId)}
  map.addEventListener('pointerup',stopTvPointer);map.addEventListener('pointercancel',stopTvPointer);
}else{
  map.addEventListener('touchstart',function(event){if(!latest||(event.target.closest&&event.target.closest('.tv-map-controls,.tv-area-switch,a')))return;const touches=event.touches;if(touches.length===1)beginTvDrag(touches[0].clientX,touches[0].clientY);else if(touches.length===2)tvPinch=Math.hypot(touches[0].clientX-touches[1].clientX,touches[0].clientY-touches[1].clientY)},false);
  map.addEventListener('touchmove',function(event){if(!latest)return;event.preventDefault();const touches=event.touches;if(touches.length===2){changeTvPinch(Math.hypot(touches[0].clientX-touches[1].clientX,touches[0].clientY-touches[1].clientY));return}if(touches.length===1)moveTvDrag(touches[0].clientX,touches[0].clientY)},false);
  map.addEventListener('touchend',clearTvInteraction,false);
  map.addEventListener('touchcancel',clearTvInteraction,false);
}
document.addEventListener('visibilitychange',function(){if(!document.hidden){rerenderMap();refresh()}});
addEventListener('pageshow',function(){rerenderMap()});
refresh();
setInterval(refresh,10000);
