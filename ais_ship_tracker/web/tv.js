const map=document.querySelector('#map');
const tiles=document.querySelector('#tiles');
const boats=document.querySelector('#boats');
const empty=document.querySelector('#empty');
const vesselList=document.querySelector('#vessel-list');
const fleetCount=document.querySelector('#fleet-count');
const feedStatus=document.querySelector('#feed-status');
const feedLight=document.querySelector('#feed-light');
const TILE=256;
let latest=null;

const apiPath=location.pathname.replace(/\/tv\/?$/,'')+'/api/status';
const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));
const escapeHtml=value=>String(value??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const midCountries={
  201:['AL','Albania'],202:['AD','Andorra'],203:['AT','Austria'],205:['BE','Belgium'],209:['CY','Cyprus'],210:['CY','Cyprus'],211:['DE','Germany'],212:['CY','Cyprus'],213:['GE','Georgia'],215:['MT','Malta'],218:['DE','Germany'],219:['DK','Denmark'],220:['DK','Denmark'],224:['ES','Spain'],225:['ES','Spain'],226:['FR','France'],227:['FR','France'],228:['FR','France'],229:['MT','Malta'],230:['FI','Finland'],231:['FO','Faroe Islands'],232:['GB','United Kingdom'],233:['GB','United Kingdom'],234:['GB','United Kingdom'],235:['GB','United Kingdom'],236:['GI','Gibraltar'],237:['GR','Greece'],238:['HR','Croatia'],239:['GR','Greece'],240:['GR','Greece'],241:['GR','Greece'],242:['MA','Morocco'],243:['HU','Hungary'],244:['NL','Netherlands'],245:['NL','Netherlands'],246:['NL','Netherlands'],247:['IT','Italy'],248:['MT','Malta'],249:['MT','Malta'],250:['IE','Ireland'],251:['IS','Iceland'],252:['LI','Liechtenstein'],253:['LU','Luxembourg'],254:['MC','Monaco'],255:['PT','Portugal'],256:['MT','Malta'],257:['NO','Norway'],258:['NO','Norway'],259:['NO','Norway'],261:['PL','Poland'],263:['PT','Portugal'],264:['RO','Romania'],265:['SE','Sweden'],266:['SE','Sweden'],267:['SK','Slovakia'],269:['CH','Switzerland'],270:['CZ','Czechia'],271:['TR','Türkiye'],272:['UA','Ukraine'],273:['RU','Russia'],274:['MK','North Macedonia'],275:['LV','Latvia'],276:['EE','Estonia'],277:['LT','Lithuania'],278:['SI','Slovenia'],279:['RS','Serbia'],301:['AI','Anguilla'],303:['US','United States'],304:['AG','Antigua and Barbuda'],305:['AG','Antigua and Barbuda'],306:['CW','Curaçao'],308:['BS','Bahamas'],309:['BS','Bahamas'],310:['BM','Bermuda'],311:['BS','Bahamas'],316:['CA','Canada'],319:['KY','Cayman Islands'],338:['US','United States'],366:['US','United States'],367:['US','United States'],368:['US','United States'],369:['US','United States'],370:['PA','Panama'],371:['PA','Panama'],372:['PA','Panama'],373:['PA','Panama'],374:['PA','Panama'],375:['VC','Saint Vincent'],376:['VC','Saint Vincent'],377:['VC','Saint Vincent'],412:['CN','China'],413:['CN','China'],414:['CN','China'],416:['TW','Taiwan'],431:['JP','Japan'],432:['JP','Japan'],440:['KR','South Korea'],441:['KR','South Korea'],477:['HK','Hong Kong'],503:['AU','Australia'],512:['NZ','New Zealand'],525:['ID','Indonesia'],533:['MY','Malaysia'],538:['MH','Marshall Islands'],563:['SG','Singapore'],564:['SG','Singapore'],565:['SG','Singapore'],566:['SG','Singapore'],601:['ZA','South Africa'],636:['LR','Liberia'],657:['NG','Nigeria'],701:['AR','Argentina'],710:['BR','Brazil'],720:['BO','Bolivia'],725:['CL','Chile'],730:['CO','Colombia'],735:['EC','Ecuador'],760:['PE','Peru']
};

function flagInfo(mmsi){
  const entry=midCountries[Number(String(mmsi).slice(0,3))];
  if(!entry)return {flag:'🏳',country:'Unknown flag'};
  return {flag:[...entry[0]].map(char=>String.fromCodePoint(127397+char.charCodeAt())).join(''),country:entry[1]};
}

function project(lat,lon,zoom){
  const scale=TILE*(2**zoom);
  const sin=clamp(Math.sin(lat*Math.PI/180),-.9999,.9999);
  return {
    x:(lon+180)/360*scale,
    y:(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*scale
  };
}

function fitView(bounds,width,height){
  const centerLat=(bounds.north+bounds.south)/2;
  const centerLon=(bounds.east+bounds.west)/2;
  let zoom=2;
  for(let candidate=18;candidate>=2;candidate--){
    const nw=project(bounds.north,bounds.west,candidate);
    const se=project(bounds.south,bounds.east,candidate);
    if(se.x-nw.x<=width*.82&&se.y-nw.y<=height*.78){zoom=candidate;break}
  }
  const center=project(centerLat,centerLon,zoom);
  return {zoom,originX:center.x-width/2,originY:center.y-height/2};
}

function renderTiles(view,width,height){
  tiles.replaceChildren();
  const count=2**view.zoom;
  const firstX=Math.floor(view.originX/TILE);
  const lastX=Math.floor((view.originX+width)/TILE);
  const firstY=Math.max(0,Math.floor(view.originY/TILE));
  const lastY=Math.min(count-1,Math.floor((view.originY+height)/TILE));
  for(let y=firstY;y<=lastY;y++){
    for(let x=firstX;x<=lastX;x++){
      const tile=document.createElement('img');
      tile.className='tile';
      tile.alt='';
      tile.decoding='async';
      tile.src=`https://tile.openstreetmap.org/${view.zoom}/${((x%count)+count)%count}/${y}.png`;
      tile.style.left=`${x*TILE-view.originX}px`;
      tile.style.top=`${y*TILE-view.originY}px`;
      tiles.appendChild(tile);
    }
  }
}

function boatNode(vessel,view){
  const point=project(Number(vessel.latitude),Number(vessel.longitude),view.zoom);
  const node=document.createElement('div');
  node.className='boat';
  node.style.left=`${point.x-view.originX}px`;
  node.style.top=`${point.y-view.originY}px`;
  const direction=Number(vessel.heading ?? vessel.cog ?? 0);
  const safeName=escapeHtml(vessel.name||`MMSI ${vessel.mmsi}`);
  node.innerHTML=`<svg class="boat-icon" viewBox="0 0 24 34" style="transform:translate(-12px,-17px) rotate(${direction}deg)"><path d="M12 1.5 21 27l-9 5.5L3 27z"/></svg><span class="boat-label">${safeName}</span>`;
  return node;
}

function vesselRow(vessel){
  const info=flagInfo(vessel.mmsi);
  const speed=Number.isFinite(Number(vessel.sog))?`${Number(vessel.sog).toFixed(1)} kn`:'—';
  const direction=Number.isFinite(Number(vessel.heading))?`${Math.round(Number(vessel.heading))}°`:Number.isFinite(Number(vessel.cog))?`${Math.round(Number(vessel.cog))}°`:'—';
  return `<article class="vessel-row"><span class="flag" title="${escapeHtml(info.country)}">${info.flag}</span><div class="vessel-main"><div class="vessel-title"><h2>${escapeHtml(vessel.name||'Unknown vessel')}</h2><strong>${speed}</strong></div><div class="vessel-meta"><span>${escapeHtml(info.country)}</span><span>MMSI ${escapeHtml(vessel.mmsi)}</span><span>${direction}</span></div><div class="vessel-dest"><span>${escapeHtml(vessel.destination||vessel.nav_status_string||'No destination broadcast')}</span><em>${escapeHtml(vessel.vessel_type||vessel.nav_status_string||'AIS contact')}</em></div></div></article>`;
}

function render(data){
  latest=data;
  const width=map.clientWidth,height=map.clientHeight;
  const bounds=data.config.bounds;
  const view=fitView(bounds,width,height);
  renderTiles(view,width,height);
  boats.replaceChildren();
  const visible=(data.vessels||[]).filter(v=>Number.isFinite(Number(v.latitude))&&Number.isFinite(Number(v.longitude))&&Number(v.latitude)>=bounds.south&&Number(v.latitude)<=bounds.north&&Number(v.longitude)>=bounds.west&&Number(v.longitude)<=bounds.east);
  visible.forEach(vessel=>boats.appendChild(boatNode(vessel,view)));
  empty.classList.toggle('show',visible.length===0);
  fleetCount.textContent=visible.length;
  vesselList.innerHTML=visible.length?visible.slice(0,8).map(vesselRow).join(''):'<div class="list-empty">No boats in the watch area</div>';
  const connected=data.connection==='Connected';
  feedLight.classList.toggle('online',connected);
  feedStatus.textContent=connected?`AISHub live · updated ${new Date(data.generated_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`:`AISHub ${String(data.connection).toLowerCase()}`;
}

async function refresh(){
  try{
    const response=await fetch(apiPath,{cache:'no-store'});
    if(!response.ok)throw new Error(`TV feed ${response.status}`);
    render(await response.json());
  }catch(error){
    empty.textContent='Waiting for AIS feed';
    empty.classList.add('show');
    console.error(error);
  }
}

let resizeTimer;
addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>latest&&render(latest),150)});
refresh();
setInterval(refresh,10000);
