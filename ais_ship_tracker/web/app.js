const $=selector=>document.querySelector(selector),$$=selector=>[...document.querySelectorAll(selector)];
const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const fmt=value=>value===null||value===undefined||value===''?'—':value;
const ago=value=>{if(!value)return'just now';const seconds=Math.max(0,(Date.now()-new Date(value).getTime())/1000);if(seconds<60)return`${Math.floor(seconds)}s ago`;if(seconds<3600)return`${Math.floor(seconds/60)}m ago`;return`${Math.floor(seconds/3600)}h ago`};
let state=null;

function showPage(id){$$('.nav').forEach(node=>node.classList.toggle('active',node.dataset.page===id));$$('.page').forEach(node=>node.classList.toggle('active',node.id===id));$('#page-title').textContent={overview:'Vessel overview',fleet:'Live vessels',area:'AIS watch area'}[id]||'Vessel overview';history.replaceState(null,'',`#${id}`);window.scrollTo({top:0,behavior:'smooth'})}
$$('.nav').forEach(node=>node.onclick=()=>showPage(node.dataset.page));$$('[data-go]').forEach(node=>node.onclick=()=>showPage(node.dataset.go));

function renderMap(vessels,bounds){const world=$('#map-world');$$('.vessel-marker').forEach(node=>node.remove());$('#map-empty').classList.toggle('hidden',vessels.length>0);const latSpan=Math.max(.0001,bounds.north-bounds.south),lonSpan=Math.max(.0001,bounds.east-bounds.west);vessels.forEach(v=>{if(typeof v.latitude!=='number'||typeof v.longitude!=='number')return;const x=Math.min(96,Math.max(4,(v.longitude-bounds.west)/lonSpan*100)),y=Math.min(94,Math.max(6,100-(v.latitude-bounds.south)/latSpan*100));const node=document.createElement('div');node.className='vessel-marker';node.style.left=`${x}%`;node.style.top=`${y}%`;node.dataset.name=v.name||'Unknown vessel';node.textContent='▲';node.title=`${v.name||'Unknown vessel'} · ${fmt(v.sog)} kn`;world.appendChild(node)})}
function vesselRow(v){return`<div class="recent-row"><div class="vessel-icon"><svg><use href="#i-ship"/></svg></div><span><b>${esc(v.name||'Unknown vessel')}</b><small>MMSI ${esc(v.mmsi)} · ${esc(v.nav_status_string||'Status unavailable')}</small></span><em>${esc(fmt(v.sog))} kn</em></div>`}
function vesselCard(v){return`<article class="vessel-card"><div class="vessel-card-top"><div class="vessel-icon"><svg><use href="#i-ship"/></svg></div><span class="status">${esc(v.nav_status_string||'Not defined')}</span></div><h3>${esc(v.name||'Unknown vessel')}</h3><span class="mmsi">MMSI ${esc(v.mmsi)} · ${esc(v.vessel_class||'Unknown class')}</span><div class="vessel-metrics"><div><small>SPEED</small><b>${esc(fmt(v.sog))} kn</b></div><div><small>COURSE</small><b>${esc(fmt(v.cog))}°</b></div><div><small>HEADING</small><b>${esc(fmt(v.heading))}°</b></div><div><small>LAST SEEN</small><b>${esc(ago(v.last_seen))}</b></div></div><div class="destination"><span>Destination</span><b>${esc(v.destination||'Not broadcast')}</b></div></article>`}

function render(data){
  state=data;
  const vessels=data.vessels||[],cfg=data.config,bounds=cfg.bounds,connected=data.connection==='Connected',feed=data.feed||{};
  $('#side-light').classList.toggle('online',connected);
  $('#hero-light').classList.toggle('online',connected);
  $('#side-status').textContent=connected?'AISHub connected':data.connection;
  $('#hero-status').textContent=connected?'Reciprocal AIS network is online':`AISHub ${String(data.connection).toLowerCase()}`;
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
  renderMap(vessels,bounds);
  $('#recent-vessels').innerHTML=vessels.length?vessels.slice(0,4).map(vesselRow).join(''):'<div class="empty">No vessel broadcasts received in this watch area yet.</div>';
  $('#fleet-grid').innerHTML=vessels.length?vessels.map(vesselCard).join(''):'<article class="panel empty">AISHub is scanning. Vessels will appear here automatically.</article>';
  const events=data.events||[];
  $('#events').innerHTML=events.length?events.slice(0,5).map(event=>`<div class="event-row"><div class="vessel-icon"><svg><use href="#i-radar"/></svg></div><span><b>${esc(event.message)}</b><small>${esc(ago(event.time))}</small></span></div>`).join(''):'<div class="empty">The operations journal is ready for the first contact.</div>';
  $('#north').textContent=bounds.north.toFixed(6);$('#south').textContent=bounds.south.toFixed(6);$('#east').textContent=bounds.east.toFixed(6);$('#west').textContent=bounds.west.toFixed(6);
  $('#class-b').textContent='AISHub network';
  $('#timeout').textContent=`${cfg.timeout_minutes} minutes`;
  $('#entities').textContent=cfg.map_entities?'Enabled':'Disabled';
}

const overviewMap=$('#sea-map'),overviewWorld=$('#map-world');
const overviewView={x:0,y:0,scale:1};
let mapDragging=false,mapPointerX=0,mapPointerY=0;
function applyOverviewView(){
  const limitX=overviewMap.clientWidth*.75,limitY=overviewMap.clientHeight*.75;
  overviewView.x=Math.max(-limitX,Math.min(limitX,overviewView.x));
  overviewView.y=Math.max(-limitY,Math.min(limitY,overviewView.y));
  overviewWorld.style.transform=`translate(${overviewView.x}px,${overviewView.y}px) scale(${overviewView.scale})`;
}
function changeOverviewZoom(amount){overviewView.scale=Math.max(.7,Math.min(3,overviewView.scale+amount));applyOverviewView()}
function changeOverviewHeight(amount){const height=Math.max(260,Math.min(720,overviewMap.getBoundingClientRect().height+amount));overviewMap.style.height=`${Math.round(height)}px`;try{localStorage.setItem('baiamonteOverviewMapHeight',String(Math.round(height)))}catch(error){}applyOverviewView()}
function resetOverviewMap(){overviewView.x=0;overviewView.y=0;overviewView.scale=1;applyOverviewView()}
overviewMap.addEventListener('pointerdown',event=>{
  if(event.target.closest('.map-controls'))return;
  const bounds=overviewMap.getBoundingClientRect();
  if(event.clientX>bounds.right-30&&event.clientY>bounds.bottom-30)return;
  mapDragging=true;mapPointerX=event.clientX;mapPointerY=event.clientY;overviewMap.classList.add('dragging');overviewMap.setPointerCapture(event.pointerId);
});
overviewMap.addEventListener('pointermove',event=>{if(!mapDragging)return;overviewView.x+=event.clientX-mapPointerX;overviewView.y+=event.clientY-mapPointerY;mapPointerX=event.clientX;mapPointerY=event.clientY;applyOverviewView()});
overviewMap.addEventListener('pointerup',event=>{mapDragging=false;overviewMap.classList.remove('dragging');if(overviewMap.hasPointerCapture(event.pointerId))overviewMap.releasePointerCapture(event.pointerId)});
overviewMap.addEventListener('pointercancel',()=>{mapDragging=false;overviewMap.classList.remove('dragging')});
overviewMap.addEventListener('wheel',event=>{event.preventDefault();changeOverviewZoom(event.deltaY<0?.15:-.15)},{passive:false});
$('#map-zoom-in').onclick=()=>changeOverviewZoom(.2);
$('#map-zoom-out').onclick=()=>changeOverviewZoom(-.2);
$('#map-size-up').onclick=()=>changeOverviewHeight(60);
$('#map-size-down').onclick=()=>changeOverviewHeight(-60);
$('#map-reset').onclick=resetOverviewMap;
try{
  const savedHeight=Number(localStorage.getItem('baiamonteOverviewMapHeight'));
  if(savedHeight>=260&&savedHeight<=720)overviewMap.style.height=`${savedHeight}px`;
  new ResizeObserver(entries=>{const height=Math.round(entries[0].contentRect.height);if(height>=260&&height<=720)localStorage.setItem('baiamonteOverviewMapHeight',String(height));applyOverviewView()}).observe(overviewMap);
}catch(error){console.debug('Map size preference is unavailable',error)}
async function refresh(){const button=$('#refresh');button.disabled=true;try{const response=await fetch('api/status',{cache:'no-store'});if(!response.ok)throw new Error(`Dashboard API ${response.status}`);render(await response.json())}catch(error){$('#receiver-status').textContent='Dashboard unavailable';$('#hero-status').textContent='Waiting for the Baiamonte AIS service';console.error(error)}finally{button.disabled=false}}
$('#refresh').onclick=refresh;showPage(location.hash.slice(1)||'overview');refresh();setInterval(refresh,10000);
