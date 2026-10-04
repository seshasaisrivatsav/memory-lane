const $ = s => document.querySelector(s);
const icon = name => `<svg aria-hidden="true"><use href="#icon-${name}"/></svg>`;
const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, page='photos', cursor=null, items=[], loading=false, generation=0, person=null, stories=[], people=[];
let viewing=[], position=0, storyMode=false, paused=false, timer=null, searchTimer=null, wasRunning=false, currentPerson=null;
let startMonth=null, monthNodes=[], timelineMonths=[], scrollFrame=0;
let previousCursor=null, loadingNewer=false, lastScrollY=0, placesMap=null, placesRequest=0, placesTimer=null, placeItems=[], placeCursor=null, placeBounds=null;
const jobNames={scan:'Library indexing',faces:'Photo & video faces',places:'Photo locations',search:'Descriptive search'};
function descriptive(){return $('#search-mode').value==='description' && !!$('#search').value.trim();}
function formatBytes(value){if(!value)return '0 B';const units=['B','KiB','MiB','GiB','TiB'];const i=Math.min(4,Math.floor(Math.log(value)/Math.log(1024)));return `${(value/1024**i).toFixed(i?1:0)} ${units[i]}`;}
async function loadStorage(){
  const target=$('#storage-details');if(!target)return;
  $('#refresh-storage').disabled=true;target.textContent='Measuring local storage…';
  try{const s=await api('/api/storage');if(!target.isConnected)return;
    target.innerHTML=`<p>Cache location</p><code class="storage-path">${escapeHTML(s.location)}</code><div class="storage-summary"><div><strong>${formatBytes(s.cache_bytes)}</strong><span>Cache & temporary files</span></div><div><strong>${formatBytes(s.total_bytes)}</strong><span>App data & downloads total</span></div></div><div class="storage-breakdown">${s.categories.map(c=>`<div class="storage-row"><div><strong>${c.label}</strong><code>${escapeHTML(c.path)}</code></div><span>${formatBytes(c.bytes)}<small>${c.files.toLocaleString()} files</small></span></div>`).join('')}</div><p class="hint">Measured ${new Date(s.measured_at).toLocaleTimeString()}. Original photos and videos are excluded. Cache includes thumbnails, previews, face images, and downloaded installers. Your browser manages its own separate cache.</p>`;
  }catch(e){target.textContent='Could not measure storage. Try Refresh usage.';toast(e.message);}finally{if($('#refresh-storage'))$('#refresh-storage').disabled=false;}
}
async function api(url, data) {
  const options=data===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Memorylane-Token':state?.token || ''},body:JSON.stringify(data)};
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),url==='/api/storage'||url.startsWith('/api/search?')?180000:30000);
  let r;
  try{r=await fetch(url,{...options,signal:controller.signal});}finally{clearTimeout(timeout);}
  let body; try{body=await r.json();}catch{throw new Error(`Request failed (${r.status})`);}
  if(!r.ok) throw new Error(body.error || 'Something went wrong');
  return body;
}
function toast(text){$('#toast').textContent=text;$('#toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.remove('show'),4500);}
function friendlyDate(value){return new Date(value).toLocaleDateString(undefined,{weekday:'short',month:'long',day:'numeric',year:'numeric'});}
function duration(n){return `${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`;}
function empty(iconName,title,description,action=''){return `<div class="empty"><div class="empty-icon">${icon(iconName)}</div><h2>${title}</h2><p>${description}</p>${action}</div>`;}
async function refreshStatus(){
  try{
    const next=await api('/api/status');
    const completed=wasRunning && !next.job.running;
    wasRunning=next.job.running;state=next;
    $('#library-count').textContent=`${state.stats.count.toLocaleString()} moments`;
    $('#library-size').textContent=`${(state.stats.bytes/1024**3).toFixed(1)} GB`;
    $('#result-count').textContent=`${state.stats.count.toLocaleString()} moments`;
    $('#rescan').disabled=state.jobs.scan.running;
    updateJob();
    if(completed){toast(state.job.errors.length ? `Finished with ${state.job.error_count||state.job.errors.length} issues. See settings.` : state.job.message);if(page!=='settings'&&!$('#viewer').open)navigate(page,person);}
  }catch(e){toast(e.message);}
}
function updateJob(){
  if(!state)return;
  const jobs=Object.values(state.jobs),active=jobs.filter(j=>j.running),banner=$('#library-progress');
  const summary=j=>j.phase==='discovering'?`${j.discovered.toLocaleString()} found`:j.phase==='waiting'?'Waiting for new files':`${j.processed.toLocaleString()} / ${j.total.toLocaleString()}`;
  if(banner){banner.hidden=!active.length;banner.textContent=active.map(j=>`${jobNames[j.kind]} · ${summary(j)}`).join('  |  ');}
  const target=$('#job-status');
  if(target){target.innerHTML=jobs.map(j=>`<div class="background-job"><div class="job-title"><strong>${jobNames[j.kind]}</strong>${j.running?`<button class="subtle" data-pause="${j.kind}">Pause</button>`:`<span class="hint">${escapeHTML(j.phase)}</span>`}</div>${j.running?`<div>${summary(j)}</div><progress ${['discovering','waiting','starting'].includes(j.phase)?'':`max="${j.total||1}" value="${j.processed}"`}></progress>`:''}<div class="hint">${escapeHTML(j.message)}</div>${j.error_count?`<details><summary>${j.error_count} issues</summary><div class="job-errors">${escapeHTML(j.errors.join('\n'))}</div></details>`:''}</div>`).join('');target.querySelectorAll('[data-pause]').forEach(b=>b.onclick=async()=>{try{await api(`/api/jobs/${b.dataset.pause}/pause`,{});b.disabled=true;toast('Pausing after the current file…');}catch(e){toast(e.message);}});}
  const save=$('#save-folders');if(save)save.disabled=!!active.length;
  for(const id of ['scan-faces','group-people']){const b=$('#'+id);if(b){b.disabled=state.jobs.faces.running;b.textContent=state.jobs.faces.running?'Recognizing faces…':'Find faces in photos & videos';}}
  if($('#index-search'))$('#index-search').disabled=state.jobs.search.running;
  if($('#index-places'))$('#index-places').disabled=state.jobs.places.running;
  if($('#search-coverage'))$('#search-coverage').textContent=`${state.stats.search_indexed.toLocaleString()} of ${state.stats.count.toLocaleString()} moments ready for descriptive search.`;
}
async function navigate(next, selectedPerson=null, month=null){
  clearTimeout(searchTimer);
  if(placesMap){placesMap.remove();placesMap=null;}clearTimeout(placesTimer);placesRequest++;
  page=next;person=selectedPerson;generation++;loading=false;loadingNewer=false;cursor=null;previousCursor=null;items=[];startMonth=month;monthNodes=[];
  $('#newer-sentinel').hidden=true;
  $('#date-rail').hidden=!['photos','videos','favorites','person'].includes(page);
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  $('#photo-page').hidden=!['photos','videos','favorites','person'].includes(page);
  $('#other-page').hidden=!$('#photo-page').hidden;
  if(!$('#photo-page').hidden){
    $('#page-title').textContent=page==='person' ? (people.find(p=>p.id===person)?.name || 'Person') : ({photos:'Photos',videos:'Videos',favorites:'Favorites'})[page];
    $('#memory-section').hidden=page!=='photos' || !!$('#search').value || !!month;
    $('.timeline-heading>span').textContent=descriptive()?'By visual similarity':'Newest first';
    $('#timeline-label').textContent=page==='photos'?'Your timeline':page==='videos'?'Your videos':page==='favorites'?'The ones you love':'Together in these moments';
    $('#gallery').innerHTML='';$('#sentinel').textContent='Loading your library…';
    if(descriptive()){$('#date-rail').hidden=true;$('#timeline-label').textContent='Closest visual matches';$('#sentinel').textContent='Searching locally… The first search may take a minute to prepare the visual index.';}else loadTimeline();
    if(page==='photos')loadStories();
    await loadMore();
    if(month)$('#gallery').scrollIntoView({block:'start'});
  }else if(page==='settings')renderSettings();
  else if(page==='people')await renderPeople();
  else if(page==='places')renderPlaces();
  else if(page==='memories')await renderMemories();
}
function filterParams(){const params=new URLSearchParams();if(page==='videos')params.set('kind','video');if(page==='favorites')params.set('favorite','1');if(person)params.set('person',person);if($('#search').value.trim())params.set('q',$('#search').value.trim());return params;}
function monthLabel(month){return new Date(month+'-01T12:00:00').toLocaleDateString(undefined,{month:'long',year:'numeric'});}
async function loadTimeline(){
  const gen=generation;
  try{const months=await api('/api/timeline?'+filterParams());if(gen!==generation)return;timelineMonths=months;let year='';
    $('#date-rail').innerHTML='<span id="rail-current"></span>'+months.map(m=>{const showYear=m.month.slice(0,4)!==year;year=m.month.slice(0,4);return `<button class="rail-dot ${showYear?'year-start':''}" data-month="${m.month}" aria-label="Jump to ${monthLabel(m.month)}, ${m.count} moments" title="${monthLabel(m.month)} · ${m.count.toLocaleString()} moments">${showYear?`<span>${year}</span>`:''}<i></i></button>`;}).join('');
    $('#date-rail').querySelectorAll('button').forEach(b=>b.onclick=()=>navigate(page,person,b.dataset.month));
    updateRail();
  }catch(e){toast(e.message);}
}
function updateRail(){
  if(!monthNodes.length)return;let low=0,high=monthNodes.length-1,active=0;
  while(low<=high){const mid=(low+high)>>1;if(monthNodes[mid].node.getBoundingClientRect().top<=180){active=mid;low=mid+1;}else high=mid-1;}
  const month=monthNodes[active].month;
  const label=$('#rail-current');if(label)label.textContent=monthLabel(month);
  document.querySelectorAll('.rail-dot').forEach(b=>{b.classList.toggle('current',b.dataset.month===month);if(b.dataset.month===month)b.setAttribute('aria-current','date');else b.removeAttribute('aria-current');});
}
window.addEventListener('scroll',()=>{const movingUp=window.scrollY<lastScrollY;lastScrollY=window.scrollY;if(movingUp&&previousCursor&&$('#newer-sentinel').getBoundingClientRect().top>0)loadNewer();if(!scrollFrame)scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;updateRail();});},{passive:true});
window.addEventListener('wheel',event=>{if(event.deltaY<0&&previousCursor&&!$('#photo-page').hidden&&$('#newer-sentinel').getBoundingClientRect().top>0)loadNewer();},{passive:true});
async function loadStories(){
  const gen=generation;
  try{
    const result=await api('/api/memories');if(gen!==generation)return;stories=result;
    if(!state.stats.count){
      $('#stories').innerHTML=`<div class="onboarding"><div><p class="eyebrow">MAKE ROOM FOR THE GOOD TIMES</p><h2>All your moments.<br>One familiar place.</h2><p>Bring your photo folders together, and rediscover the little things worth remembering.</p><button class="primary" id="onboard">Connect your folders &nbsp; ↗</button></div><div class="illustration" aria-hidden="true"><div class="print"><div></div></div><div class="print"><div></div></div></div></div>`;
      $('#onboard').onclick=()=>navigate('settings');return;
    }
    renderStoryCards($('#stories'));
  }catch(e){toast(e.message);}
}
function renderStoryCards(target){
  if(!stories.length){target.innerHTML=`<div class="empty-story">${icon('spark')}<div><h2>A past moment, waiting to return</h2><p>Stories appear when photos match this time of year — from one year ago, two years ago, and beyond.</p></div></div>`;return;}
  target.innerHTML=stories.map((s,i)=>`<button class="story" data-story="${i}"><img alt="" src="/media/${s.items[0].id}/thumb"><span class="story-badge">${icon('spark')}</span><span class="story-text"><strong>${escapeHTML(s.title)}</strong><small>${s.subtitle} · ${s.items.length} moments</small></span></button>`).join('');
  target.querySelectorAll('[data-story]').forEach(b=>b.onclick=()=>openViewer(stories[+b.dataset.story].items,0,true));
}
async function loadMore(){
  if(loading||loadingNewer)return;loading=true;const gen=generation;
  try{
  const params=filterParams();
  if(cursor)params.set('cursor',cursor);
  if(startMonth)params.set('from_month',startMonth);
    const result=await api((descriptive()?'/api/search?':'/api/media?')+params);if(gen!==generation)return;
    if(!items.length){previousCursor=result.previous;updateNewer();}
    cursor=result.next;items.push(...result.items);
    if(descriptive()){
      let grid=$('#semantic-grid');if(!grid){grid=document.createElement('div');grid.id='semantic-grid';grid.className='photo-grid';$('#gallery').append(grid);}
      for(const item of result.items)grid.append(makeTile(item));
    }else{
    for(const item of result.items){
      const month=item.captured.slice(0,7);
      if(!monthNodes.length || monthNodes[monthNodes.length-1].month!==month){const heading=document.createElement('h2');heading.className='month-heading';heading.textContent=monthLabel(month);heading.id='month-'+month;$('#gallery').append(heading);monthNodes.push({month,node:heading});}
      const date=item.captured.slice(0,10);
      let grid=$(`#day-${date}`);
      if(!grid){const title=document.createElement('h3');title.className='date-heading';title.textContent=friendlyDate(item.captured);$('#gallery').append(title);grid=document.createElement('div');grid.className='photo-grid';grid.id='day-'+date;$('#gallery').append(grid);}
      grid.append(makeTile(item));
    }
    }
    $('#sentinel').textContent=cursor?'Loading more moments…':items.length?'You’re all caught up':'';
    updateRail();
    if(!items.length){
      const text=$('#search').value ? ['search','No moments found',descriptive()?'Build the descriptive search index in Settings. Results cover indexed photos and video poster frames.':'Try a filename or a date, such as 2024-07.'] : page==='favorites'?['heart','Keep your favorites close','Open a photo or video and tap the heart to save it here.']:page==='videos'?['video','Your home movies belong here','Videos from your connected folders will appear here.']:['photos','Your story starts here','Add your folders in settings. We’ll gather your photos and videos into a timeline.'];
      $('#gallery').innerHTML=empty(...text);
    }
  }catch(e){if(gen===generation){$('#sentinel').textContent='Could not load photos. Refresh to retry.';toast(e.message);}}
  finally{if(gen===generation){loading=false;if(cursor && $('#sentinel').getBoundingClientRect().top<innerHeight+600)setTimeout(loadMore,20);}}
}
function makeTile(item,list=null){const button=document.createElement('button');button.className='photo';button.dataset.id=item.id;button.setAttribute('aria-label',`Open ${item.name}`);button.innerHTML=`<img loading="lazy" src="/media/${item.id}/thumb" alt="${escapeHTML(item.name)}">${item.kind==='video'?`<span class="duration">▷ ${duration(item.duration)}</span>`:''}${item.favorite?'<span class="fav-badge">♥</span>':''}`;button.onclick=()=>{const current=list||items;openViewer(current,current.indexOf(item));};return button;}
function updateNewer(){$('#newer-sentinel').hidden=!previousCursor;$('#load-newer').disabled=loadingNewer;$('#load-newer').textContent=loadingNewer?'Loading newer moments…':'↑ Newer moments · scroll up to continue';}
async function loadNewer(){
  if(!previousCursor||loading||loadingNewer||$('#photo-page').hidden)return;
  loadingNewer=true;updateNewer();const gen=generation;
  try{const params=filterParams();params.set('newer',previousCursor);const result=await api('/api/media?'+params);if(gen!==generation)return;
    const anchor=$('#gallery .photo'),anchorTop=anchor?.getBoundingClientRect().top,anchorId=anchor?.dataset.id;
    const seen=new Set(items.map(i=>i.id));items.unshift(...result.items.filter(i=>!seen.has(i.id)));previousCursor=result.previous;
    const tiles=new Map([...document.querySelectorAll('#gallery .photo')].map(el=>[el.dataset.id,el]));const fragment=document.createDocumentFragment();monthNodes=[];let currentDate='',grid=null;
    for(const item of items){const month=item.captured.slice(0,7),date=item.captured.slice(0,10);if(!monthNodes.length||monthNodes.at(-1).month!==month){const h=document.createElement('h2');h.className='month-heading';h.id='month-'+month;h.textContent=monthLabel(month);fragment.append(h);monthNodes.push({month,node:h});}if(currentDate!==date){currentDate=date;const h=document.createElement('h3');h.className='date-heading';h.textContent=friendlyDate(item.captured);grid=document.createElement('div');grid.className='photo-grid';grid.id='day-'+date;fragment.append(h,grid);}grid.append(tiles.get(item.id)||makeTile(item));}
    $('#gallery').replaceChildren(fragment);updateNewer();
    if(anchorId){const restored=$(`#gallery .photo[data-id="${anchorId}"]`);window.scrollBy(0,restored.getBoundingClientRect().top-anchorTop);lastScrollY=window.scrollY;}
    updateRail();
  }catch(e){toast(e.message);}finally{if(gen===generation){loadingNewer=false;updateNewer();}}
}
$('#load-newer').onclick=loadNewer;
new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&cursor&&!$('#photo-page').hidden)loadMore();},{rootMargin:'700px'}).observe($('#sentinel'));
function renderSettings(){
  $('#other-page').innerHTML=`<div class="section-top"><div><p class="eyebrow">A LITTLE ORGANIZATION</p><h1>Folders & settings</h1></div></div><div class="settings-card"><h2>Your photo folders</h2><p>Add all your folders at once, one path per line or separated by commas. Subfolders are included automatically. Your originals are never moved or changed.</p><label class="hint" for="folder-paths">FOLDER PATHS</label><textarea id="folder-paths" spellcheck="false" placeholder="G:\\Photos&#10;D:\\Family videos">${escapeHTML(state.folders.join('\n'))}</textarea><div class="form-actions"><span class="hint">Removing a folder only removes it from this library.</span><button class="primary" id="save-folders" data-job-button>Save & scan folders</button></div><div id="job-status" class="job-status" role="status"></div></div><div class="settings-card" id="storage-card"><div class="section-title"><h2>Cache & disk usage</h2><button class="subtle" id="refresh-storage">Refresh usage</button></div><div id="storage-details" aria-live="polite">Measuring local storage…</div></div><div class="settings-card"><h2>People, together</h2><p>Find faces in photos and videos on this computer. Add names and merge duplicate groups from People. Automatic groups can be imperfect, especially across ages.</p><button class="subtle" id="scan-faces" data-job-button>Find & group faces</button><p class="hint">${state.faces_ready?'Local face models are installed.':'Face models are missing. Run setup.ps1 in the project folder.'} Photos and sampled video frames are processed locally alongside scanning. Video sampling is capped at 24 frames per clip; brief appearances can be missed.</p></div><div class="settings-card"><h2>Search by description</h2><p>Try “a beach at sunset” or “a dog on a sofa”. A local CLIP model compares your words with indexed photos and video poster frames. Results are approximate, not exact object labels.</p><p id="search-coverage" class="hint"></p><button class="subtle" id="index-search">Build descriptive search index</button><p class="hint">The first pass runs in the background. Search covers processed items immediately. All model inference stays on this device.</p></div><div class="settings-card"><h2>The way memories work</h2><p>Stories look back one year, two years, and further, within seven days of today’s anniversary. Each photo plays for five seconds; videos play to the end. Capture dates are preferred, with file dates used when unavailable.</p><p class="hint">${state.ffmpeg?'Video thumbnail support is available.':'Install FFmpeg and add it to PATH for video thumbnails and capture dates.'} Playback depends on your browser’s codec support; originals are not transcoded.</p></div><div class="settings-card"><h2>Made to stay local</h2><p>Photos, thumbnails, names, and face data stay on this device. The app is available only on localhost. Use Refresh library after adding or changing files in a connected folder.</p></div>`;
  $('#save-folders').onclick=async()=>{try{await api('/api/settings',{folder_text:$('#folder-paths').value});await refreshStatus();toast('Folders saved. Building your timeline…');}catch(e){toast(e.message);}};
  $('#scan-faces').onclick=runFaces;$('#index-search').onclick=()=>runBackground('search');updateJob();$('#refresh-storage').onclick=loadStorage;loadStorage();
}
async function runFaces(){try{const job=await api('/api/faces',{});await refreshStatus();toast('Recognizing photo and video faces alongside indexing…');}catch(e){toast(e.message);}}
async function renderPeople(){
  const gen=generation;
  try{const result=await api('/api/people');if(gen!==generation)return;people=result;
    $('#other-page').innerHTML=`<div class="section-top"><div><p class="eyebrow">FAMILIAR FACES</p><h1>People</h1></div><button class="subtle" id="group-people" data-job-button>Find & group faces</button></div><p class="hint">Groups are automatic and may split across ages or angles. Review likely duplicates below, or use Edit person to merge groups.</p><section class="duplicate-review"><button class="subtle" id="review-duplicates">Review likely duplicates</button><div id="duplicate-pairs" class="duplicate-pairs"></div></section>${people.length?`<div class="people-grid">${people.map(p=>`<div class="person"><button data-person="${p.id}"><img src="/face/${p.crop}" alt="${escapeHTML(p.name || 'Unnamed person')}"><strong>${escapeHTML(p.name || 'Unnamed person')}</strong><span>${p.count} moments · ${p.videos} videos</span></button><button class="text-button" data-name="${p.id}">${p.name?'Edit person':'Add a name'}</button></div>`).join('')}</div>`:empty('people','Find the familiar faces','Connect your folders, then run face grouping to bring photos of the same person together.')}<div id="job-status" class="job-status"></div>`;
    $('#group-people').onclick=runFaces;
    $('#review-duplicates').onclick=reviewDuplicates;
    document.querySelectorAll('[data-person]').forEach(b=>b.onclick=()=>navigate('person',+b.dataset.person));
    document.querySelectorAll('[data-name]').forEach(b=>b.onclick=()=>editPerson(+b.dataset.name));updateJob();
  }catch(e){toast(e.message);}
}
async function reviewDuplicates(){
  const button=$('#review-duplicates');button.disabled=true;
  try{
    const pairs=await api('/api/people/suggestions');
    if(page!=='people')return;
    $('#duplicate-pairs').innerHTML=pairs.length?pairs.map(pair=>{
      const a=people.find(p=>p.id===pair.source),b=people.find(p=>p.id===pair.target);
      if(!a||!b)return '';
      return `<div class="duplicate-pair"><img src="/face/${a.crop}" alt="${escapeHTML(a.name||'First group')}"><img src="/face/${b.crop}" alt="${escapeHTML(b.name||'Second group')}"><p>${escapeHTML(a.name||'Unnamed')} · ${a.count} moments<br>${escapeHTML(b.name||'Unnamed')} · ${b.count} moments</p><button class="subtle" data-merge-source="${a.id}" data-merge-target="${b.id}">Same person — merge</button></div>`;
    }).join(''):'<p class="hint">No likely duplicates found. You can still merge groups through Edit person.</p>';
    document.querySelectorAll('[data-merge-source]').forEach(b=>b.onclick=async()=>{
      try{await api(`/api/people/${b.dataset.mergeSource}/merge`,{target:+b.dataset.mergeTarget});await renderPeople();await reviewDuplicates();toast('Groups merged.');}catch(e){toast(e.message);}
    });
  }catch(e){toast(e.message);}finally{button.disabled=false;}
}
function editPerson(id){currentPerson=id;$('#person-name').value=people.find(p=>p.id===id).name;$('#merge-target').innerHTML='<option value="">Choose a group</option>'+people.filter(p=>p.id!==id).map(p=>`<option value="${p.id}">${escapeHTML(p.name||`Unnamed person ${p.id}`)} · ${p.count} moments · ${p.videos} videos</option>`).join('');$('#person-dialog').showModal();$('#person-name').focus();}
$('#save-person').onclick=async e=>{e.preventDefault();try{await api(`/api/people/${currentPerson}`,{name:$('#person-name').value});$('#person-dialog').close();renderPeople();}catch(e){toast(e.message);}};
$('#merge-person').onclick=async()=>{const target=+$('#merge-target').value;if(!target)return toast('Choose a group first.');try{await api(`/api/people/${currentPerson}/merge`,{target});$('#person-dialog').close();renderPeople();}catch(e){toast(e.message);}};
async function renderPlaces(){
  $('#other-page').innerHTML=`<div class="section-top"><div><p class="eyebrow">EVERYWHERE YOU’VE BEEN</p><h1>Places</h1></div><button class="subtle" id="index-places">Find photo locations</button></div><p class="hint">Locations from the GPS tags in your photos. This map works offline: no coordinates or photos are sent to a map provider. Country outlines are included; street-level tiles are not.</p><div id="places-map" aria-label="Map of photo locations"></div><div class="map-caption"><span id="places-count">Loading locations…</span><button id="refresh-places" class="text-button">Refresh map</button></div><div id="place-selection"><p class="hint">Choose a dot to explore the photos taken there. Zoom to separate nearby locations.</p></div><div id="job-status" class="job-status"></div>`;
  $('#index-places').onclick=()=>runBackground('places');$('#refresh-places').onclick=()=>refreshMap();updateJob();
  const map=L.map('places-map',{preferCanvas:true,minZoom:1,maxZoom:18,maxBounds:[[-85,-180],[85,180]],maxBoundsViscosity:1}).setView([22,0],2);placesMap=map;
  map.attributionControl.addAttribution('Country outlines: <a href="https://www.naturalearthdata.com/" target="_blank" rel="noreferrer">Natural Earth</a>');
  try{const world=await api('/static/vendor/world.geojson');if(placesMap!==map)return;L.geoJSON(world,{style:{color:'#607667',weight:.6,fillColor:'#354c40',fillOpacity:1},onEachFeature:(feature,layer)=>layer.bindTooltip(feature.properties.name||feature.properties.NAME_EN||feature.properties.ADMIN||'')}).addTo(map);await refreshMap(true);}catch(e){toast(e.message);}
  map.on('moveend',()=>{clearTimeout(placesTimer);placesTimer=setTimeout(()=>refreshMap(),180);});
}
let mapMarkers=null;
async function refreshMap(fit=false){
  const map=placesMap;if(!map)return;const seq=++placesRequest;
  const bounds=map.getBounds(),params=new URLSearchParams({zoom:map.getZoom(),south:Math.max(-90,bounds.getSouth()),north:Math.min(90,bounds.getNorth()),west:Math.max(-180,bounds.getWest()),east:Math.min(180,bounds.getEast())});
  try{const result=await api('/api/places?'+params);if(placesMap!==map||seq!==placesRequest)return;
    if(mapMarkers)mapMarkers.remove();mapMarkers=L.layerGroup().addTo(map);
    for(const group of result.groups){L.circleMarker([group.latitude,group.longitude],{radius:Math.min(19,5+Math.log2(group.count+1)*1.5),weight:1.5,color:'#e0ebcf',fillColor:'#a7c893',fillOpacity:.83}).bindTooltip(`${group.count.toLocaleString()} photo${group.count===1?'':'s'}`).on('click',()=>showPlace(group)).addTo(mapMarkers);}
    $('#places-count').textContent=result.summary.count?`${result.summary.count.toLocaleString()} geotagged photos · ${result.groups.length} visible locations${result.truncated?' · zoom in for more':''}`:'No geotagged photos indexed yet. Choose Find photo locations.';
    if(fit&&result.summary.count){const s=result.summary;map.fitBounds([[s.south,s.west],[s.north,s.east]],{padding:[28,28],maxZoom:12});}
  }catch(e){toast(e.message);}
}
async function showPlace(group){placeBounds=group;placeCursor=null;placeItems=[];$('#place-selection').innerHTML=`<div class="section-title"><h2>${group.count.toLocaleString()} photos in this area</h2><span class="hint">${group.latitude.toFixed(4)}, ${group.longitude.toFixed(4)}</span></div><div id="place-photos" class="photo-grid"></div><button id="more-place" class="subtle">Load photos</button>`;$('#more-place').onclick=loadPlacePhotos;await loadPlacePhotos();}
async function loadPlacePhotos(){
  const group=placeBounds;if(!group||!$('#more-place'))return;$('#more-place').disabled=true;
  const params=new URLSearchParams({south:group.south,north:group.north,west:group.west,east:group.east});if(placeCursor)params.set('cursor',placeCursor);
  try{const result=await api('/api/places/media?'+params);if(page!=='places'||group!==placeBounds)return;placeItems.push(...result.items);placeCursor=result.next;for(const item of result.items)$('#place-photos').append(makeTile(item,placeItems));$('#more-place').hidden=!placeCursor;$('#more-place').textContent='Load more photos';}catch(e){toast(e.message);}finally{if($('#more-place'))$('#more-place').disabled=false;}
}
async function runBackground(kind){try{await api(`/api/${kind}/index`,{});await refreshStatus();toast(`${jobNames[kind]} started locally.`);}catch(e){toast(e.message);}}
async function renderMemories(){const gen=generation;try{const result=await api('/api/memories');if(gen!==generation)return;stories=result;$('#other-page').innerHTML=`<div class="section-top"><div><p class="eyebrow">WORTH ANOTHER LOOK</p><h1>Memories</h1></div></div><p class="hint">This time, another year. Little stories from your own library.</p><div id="all-stories" class="stories"></div>`;renderStoryCards($('#all-stories'));}catch(e){toast(e.message);}}
function openViewer(list,index,story=false){viewing=list;position=index;storyMode=story;paused=false;$('#viewer').showModal();showItem();}
function showItem(){
  clearTimeout(timer);const item=viewing[position];if(!item)return;
  $('#viewer-title').textContent=item.name;$('#viewer-date').textContent=friendlyDate(item.captured);
  $('#viewer-position').textContent=`${position+1} / ${viewing.length}${storyMode?' · Memory':''}`;
  $('#viewer-meta').textContent=`${item.width} × ${item.height} · ${item.date_source}`;
  $('#favorite').classList.toggle('selected',!!item.favorite);$('#favorite').setAttribute('aria-pressed',!!item.favorite);
  $('#play-story').hidden=!storyMode;$('#play-story').textContent=paused?'Play':'Pause';
  $('#previous').disabled=position===0;$('#next').disabled=position===viewing.length-1;
  $('#story-progress').innerHTML=storyMode?viewing.map((_,i)=>`<span class="${i<=position?'done':''}"></span>`).join(''):'';
  $('#viewer-content').replaceChildren();
  const media=document.createElement(item.kind==='video'?'video':'img');media.src=`/media/${item.id}/view`;
  media.onerror=()=>{clearTimeout(timer);$('#viewer-content').innerHTML='<p>This file can’t be played in your browser.<br>The original is unchanged. Try a browser-supported video codec or check that the folder is connected.</p>';};
  if(item.kind==='video'){media.controls=true;media.autoplay=!paused;media.playsInline=true;media.onloadedmetadata=()=>{if(item.face_time)media.currentTime=item.face_time;};media.onended=()=>{if(storyMode&&!paused)advanceStory();};}
  else{media.alt=item.name;media.onload=()=>{if(storyMode&&!paused)timer=setTimeout(advanceStory,5000);};}
  $('#viewer-content').append(media);
}
function advanceStory(){if(position<viewing.length-1){position++;showItem();}else{paused=true;$('#play-story').textContent='Replay';}}
function closeViewer(){clearTimeout(timer);$('#viewer-content').replaceChildren();$('#viewer').close();}
$('#close-viewer').onclick=closeViewer;$('#viewer').addEventListener('cancel',e=>{e.preventDefault();closeViewer();});
$('#previous').onclick=()=>{if(position>0){position--;showItem();}};
$('#next').onclick=()=>{if(position<viewing.length-1){position++;showItem();}};
$('#play-story').onclick=()=>{if($('#play-story').textContent==='Replay'){position=0;paused=false;showItem();return;}paused=!paused;$('#play-story').textContent=paused?'Play':'Pause';clearTimeout(timer);const v=$('#viewer-content video');if(v){if(paused)v.pause();else v.play().catch(()=>{});}else if(!paused)timer=setTimeout(advanceStory,5000);};
$('#favorite').onclick=async()=>{try{const item=viewing[position];const value=await api(`/api/media/${item.id}/favorite`,{});item.favorite=value.favorite;$('#favorite').classList.toggle('selected',!!value.favorite);$('#favorite').setAttribute('aria-pressed',!!value.favorite);}catch(e){toast(e.message);}};
$('#viewer').addEventListener('close',()=>{clearTimeout(timer);if(page==='favorites')navigate(page);});
document.addEventListener('keydown',e=>{if($('#viewer').open){if(e.key==='ArrowRight')$('#next').click();if(e.key==='ArrowLeft')$('#previous').click();return;}if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('#search').focus();}});
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{$('#search').value='';navigate(b.dataset.page);});
$('#settings-top').onclick=()=>navigate('settings');$('#see-memories').onclick=()=>navigate('memories');
$('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>navigate(['photos','videos','favorites','person'].includes(page)?page:'photos',person),300);};
$('#search-mode').onchange=()=>navigate(['photos','videos','favorites','person'].includes(page)?page:'photos',person);
$('#rescan').onclick=async()=>{try{await api('/api/scan',{});await refreshStatus();toast('Refreshing your library…');}catch(e){toast(e.message);}};
async function pollStatus(){await refreshStatus();setTimeout(pollStatus,2500);}
(async()=>{await refreshStatus();setTimeout(pollStatus,2500);if(state){await navigate('photos');}else{$('#gallery').innerHTML=empty('folder','Couldn’t connect','Start the local app, then refresh this page.');}})();
