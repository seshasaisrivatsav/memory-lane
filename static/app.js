const $ = s => document.querySelector(s);
const icon = name => `<svg aria-hidden="true"><use href="#icon-${name}"/></svg>`;
const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, page='photos', cursor=null, items=[], loading=false, generation=0, person=null, stories=[], people=[];
let viewing=[], position=0, storyMode=false, paused=false, timer=null, searchTimer=null, wasRunning=false, currentPerson=null;
let startMonth=null, monthNodes=[], timelineMonths=[], scrollFrame=0;
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
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),url==='/api/storage'?120000:30000);
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
    $('#rescan').disabled=state.job.running;
    updateJob();
    if(completed){toast(state.job.errors.length ? `Finished with ${state.job.error_count||state.job.errors.length} issues. See settings.` : state.job.message);if(page!=='settings'&&!$('#viewer').open)navigate(page,person);}
  }catch(e){toast(e.message);}
}
function updateJob(){
  if(!state)return;
  const j=state.job;
  const banner=$('#library-progress');
  if(banner){banner.hidden=!j.running;banner.textContent=j.phase==='discovering'?`Discovering files · ${(j.discovered||0).toLocaleString()} found`:j.running?`${j.kind==='faces'?'Grouping faces':'Updating library'} · ${j.total?Math.floor(j.processed/j.total*100):0}% · ${j.processed.toLocaleString()} / ${j.total.toLocaleString()}`:'';}
  const target=$('#job-status');if(!target)return;
  target.innerHTML=`<strong>${j.running ? (j.kind==='faces'?'Grouping faces':j.phase==='discovering'?'Discovering your photos and videos':'Updating your library') : escapeHTML(j.message)}</strong>${j.running?`<div>${j.phase==='discovering'?`${(j.discovered||0).toLocaleString()} media files found`:`${j.processed.toLocaleString()} of ${j.total.toLocaleString()} files`}</div><progress ${j.phase==='discovering'?'':`max="${j.total||1}" value="${j.processed}"`}></progress><div class="hint">${escapeHTML(j.message)}</div>`:''}${j.errors.length?`<details><summary>${j.error_count||j.errors.length} files or folders need attention</summary><div class="job-errors">${escapeHTML(j.errors.join('\n'))}${j.error_count>j.errors.length?'\nShowing the first 100 issues.':''}</div></details>`:''}`;
  document.querySelectorAll('[data-job-button]').forEach(b=>b.disabled=j.running);
  for(const id of ['scan-faces','group-people']){const b=$('#'+id);if(b){b.disabled=(j.running&&j.kind==='faces')||j.queued_faces;b.textContent=j.queued_faces?'Face grouping queued after scan':j.running&&j.kind==='scan'?'Group faces after scan':'Find & group faces';}}
}
async function navigate(next, selectedPerson=null, month=null){
  page=next;person=selectedPerson;generation++;loading=false;cursor=null;items=[];startMonth=month;monthNodes=[];
  $('#date-rail').hidden=!['photos','videos','favorites','person'].includes(page);
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  $('#photo-page').hidden=!['photos','videos','favorites','person'].includes(page);
  $('#other-page').hidden=!$('#photo-page').hidden;
  if(!$('#photo-page').hidden){
    $('#page-title').textContent=page==='person' ? (people.find(p=>p.id===person)?.name || 'Person') : ({photos:'Photos',videos:'Videos',favorites:'Favorites'})[page];
    $('#memory-section').hidden=page!=='photos' || !!$('#search').value;
    $('#timeline-label').textContent=page==='photos'?'Your timeline':page==='videos'?'Your videos':page==='favorites'?'The ones you love':'Together in these moments';
    $('#gallery').innerHTML='';$('#sentinel').textContent='Loading your library…';
    loadTimeline();
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
window.addEventListener('scroll',()=>{if(!scrollFrame)scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;updateRail();});},{passive:true});
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
  if(loading)return;loading=true;const gen=generation;
  try{
  const params=filterParams();
  if(cursor)params.set('cursor',cursor);
  if(startMonth)params.set('from_month',startMonth);
    const result=await api('/api/media?'+params);if(gen!==generation)return;
    cursor=result.next;items.push(...result.items);
    for(const item of result.items){
      const month=item.captured.slice(0,7);
      if(!monthNodes.length || monthNodes[monthNodes.length-1].month!==month){const heading=document.createElement('h2');heading.className='month-heading';heading.textContent=monthLabel(month);heading.id='month-'+month;$('#gallery').append(heading);monthNodes.push({month,node:heading});}
      const date=item.captured.slice(0,10);
      let grid=$(`#day-${date}`);
      if(!grid){const title=document.createElement('h3');title.className='date-heading';title.textContent=friendlyDate(item.captured);$('#gallery').append(title);grid=document.createElement('div');grid.className='photo-grid';grid.id='day-'+date;$('#gallery').append(grid);}
      const button=document.createElement('button');button.className='photo';button.setAttribute('aria-label',`Open ${item.name}`);
      button.innerHTML=`<img loading="lazy" src="/media/${item.id}/thumb" alt="${escapeHTML(item.name)}">${item.kind==='video'?`<span class="duration">▷ ${duration(item.duration)}</span>`:''}${item.favorite?'<span class="fav-badge">♥</span>':''}`;
      button.onclick=()=>openViewer(items,items.indexOf(item));grid.append(button);
    }
    $('#sentinel').textContent=cursor?'Loading more moments…':items.length?'You’re all caught up':'';
    updateRail();
    if(!items.length){
      const text=$('#search').value ? ['search','No moments found','Try a filename or a date, such as 2024-07.'] : page==='favorites'?['heart','Keep your favorites close','Open a photo or video and tap the heart to save it here.']:page==='videos'?['video','Your home movies belong here','Videos from your connected folders will appear here.']:['photos','Your story starts here','Add your folders in settings. We’ll gather your photos and videos into a timeline.'];
      $('#gallery').innerHTML=empty(...text);
    }
  }catch(e){if(gen===generation){$('#sentinel').textContent='Could not load photos. Refresh to retry.';toast(e.message);}}
  finally{if(gen===generation){loading=false;if(cursor && $('#sentinel').getBoundingClientRect().top<innerHeight+600)setTimeout(loadMore,20);}}
}
new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&cursor&&!$('#photo-page').hidden)loadMore();},{rootMargin:'700px'}).observe($('#sentinel'));
function renderSettings(){
  $('#other-page').innerHTML=`<div class="section-top"><div><p class="eyebrow">A LITTLE ORGANIZATION</p><h1>Folders & settings</h1></div></div><div class="settings-card"><h2>Your photo folders</h2><p>Add all your folders at once, one path per line or separated by commas. Subfolders are included automatically. Your originals are never moved or changed.</p><label class="hint" for="folder-paths">FOLDER PATHS</label><textarea id="folder-paths" spellcheck="false" placeholder="G:\\Photos&#10;D:\\Family videos">${escapeHTML(state.folders.join('\n'))}</textarea><div class="form-actions"><span class="hint">Removing a folder only removes it from this library.</span><button class="primary" id="save-folders" data-job-button>Save & scan folders</button></div><div id="job-status" class="job-status" role="status"></div></div><div class="settings-card" id="storage-card"><div class="section-title"><h2>Cache & disk usage</h2><button class="subtle" id="refresh-storage">Refresh usage</button></div><div id="storage-details" aria-live="polite">Measuring local storage…</div></div><div class="settings-card"><h2>People, together</h2><p>Find faces and group similar ones on this computer. Add names and merge duplicate groups from People. Automatic groups can be imperfect, especially across ages.</p><button class="subtle" id="scan-faces" data-job-button>Find & group faces</button><p class="hint">${state.faces_ready?'Local face models are installed.':'Face models are missing. Run setup.ps1 in the project folder.'} Face grouping processes photos, not video frames.</p></div><div class="settings-card"><h2>The way memories work</h2><p>Stories look back one year, two years, and further, within seven days of today’s anniversary. Each photo plays for five seconds; videos play to the end. Capture dates are preferred, with file dates used when unavailable.</p><p class="hint">${state.ffmpeg?'Video thumbnail support is available.':'Install FFmpeg and add it to PATH for video thumbnails and capture dates.'} Playback depends on your browser’s codec support; originals are not transcoded.</p></div><div class="settings-card"><h2>Made to stay local</h2><p>Photos, thumbnails, names, and face data stay on this device. The app is available only on localhost. Use Refresh library after adding or changing files in a connected folder.</p></div>`;
  $('#save-folders').onclick=async()=>{try{await api('/api/settings',{folder_text:$('#folder-paths').value});await refreshStatus();toast('Folders saved. Building your timeline…');}catch(e){toast(e.message);}};
  $('#scan-faces').onclick=runFaces;updateJob();$('#refresh-storage').onclick=loadStorage;loadStorage();
}
async function runFaces(){try{const job=await api('/api/faces',{});await refreshStatus();toast(job.queued_faces?'Face grouping will start when this scan finishes.':'Finding familiar faces…');}catch(e){toast(e.message);}}
async function renderPeople(){
  const gen=generation;
  try{const result=await api('/api/people');if(gen!==generation)return;people=result;
    $('#other-page').innerHTML=`<div class="section-top"><div><p class="eyebrow">FAMILIAR FACES</p><h1>People</h1></div><button class="subtle" id="group-people" data-job-button>Find & group faces</button></div><p class="hint">The people who make your moments. Add a name, or merge groups that belong together.</p>${people.length?`<div class="people-grid">${people.map(p=>`<div class="person"><button data-person="${p.id}"><img src="/face/${p.crop}" alt="${escapeHTML(p.name || 'Unnamed person')}"><strong>${escapeHTML(p.name || 'Unnamed person')}</strong><span>${p.count} photos</span></button><button class="text-button" data-name="${p.id}">${p.name?'Edit person':'Add a name'}</button></div>`).join('')}</div>`:empty('people','Find the familiar faces','Connect your folders, then run face grouping to bring photos of the same person together.')}<div id="job-status" class="job-status"></div>`;
    $('#group-people').onclick=runFaces;
    document.querySelectorAll('[data-person]').forEach(b=>b.onclick=()=>navigate('person',+b.dataset.person));
    document.querySelectorAll('[data-name]').forEach(b=>b.onclick=()=>editPerson(+b.dataset.name));updateJob();
  }catch(e){toast(e.message);}
}
function editPerson(id){currentPerson=id;$('#person-name').value=people.find(p=>p.id===id).name;$('#merge-target').innerHTML='<option value="">Choose a group</option>'+people.filter(p=>p.id!==id).map(p=>`<option value="${p.id}">${escapeHTML(p.name||`Unnamed person ${p.id}`)} · ${p.count} photos</option>`).join('');$('#person-dialog').showModal();$('#person-name').focus();}
$('#save-person').onclick=async e=>{e.preventDefault();try{await api(`/api/people/${currentPerson}`,{name:$('#person-name').value});$('#person-dialog').close();renderPeople();}catch(e){toast(e.message);}};
$('#merge-person').onclick=async()=>{const target=+$('#merge-target').value;if(!target)return toast('Choose a group first.');try{await api(`/api/people/${currentPerson}/merge`,{target});$('#person-dialog').close();renderPeople();}catch(e){toast(e.message);}};
function renderPlaces(){$('#other-page').innerHTML=`<div class="section-top"><div><p class="eyebrow">EVERYWHERE YOU’VE BEEN</p><h1>Places</h1></div><span class="hint">COMING LATER</span></div><div class="empty"><div class="place-preview" aria-hidden="true">${icon('pin')}</div><h2>Your memories, on the map</h2><p>A place for exploring photos by their GPS locations. This is a preview of a future feature — maps and location processing are not enabled.</p><button class="subtle" id="back-photos">Back to photos</button></div>`;$('#back-photos').onclick=()=>navigate('photos');}
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
  if(item.kind==='video'){media.controls=true;media.autoplay=!paused;media.playsInline=true;media.onended=()=>{if(storyMode&&!paused)advanceStory();};}
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
$('#rescan').onclick=async()=>{try{await api('/api/scan',{});await refreshStatus();toast('Refreshing your library…');}catch(e){toast(e.message);}};
async function pollStatus(){await refreshStatus();setTimeout(pollStatus,2500);}
(async()=>{await refreshStatus();setTimeout(pollStatus,2500);if(state){await navigate('photos');}else{$('#gallery').innerHTML=empty('folder','Couldn’t connect','Start the local app, then refresh this page.');}})();
