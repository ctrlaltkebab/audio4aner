'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {audio:'Аудио',script:'Сценарии',author:'Авторы'};
const stateLabels = {metadata:'Текст сохранён',partial:'Сохранено частично',complete:'Всё сохранено'};
const taskLabels = {queued:'Ожидает сохранения текста',reading:'Получение карточки',media:'Текст сохранён · ожидают файлы',downloading:'Загрузка файлов',done:'Готово',error:'Нужен повтор загрузки'};
let token = '', info = {counts:{},tags:[]}, generation = 0, listState = {}, toastTimer, pendingTag = '';
let track = null, lastPositionSave = 0, lastQueueSignature = '';
let queueOffset = 0;
const audio = $('#audio');

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Archive-Token':token},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос');
  return data;
}
function toast(text) { $('#toast').textContent=text; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,5500); }
function entryHash(key) { return '#entry/' + encodeURIComponent(key); }
function duration(value) { value=Math.floor(Number(value)||0);return Math.floor(value/60)+':'+String(value%60).padStart(2,'0'); }
function tagHtml(t, filter=false) { return `<${filter?'button':'span'} class="tag ${filter?'filter-chip':''}" data-category="${esc(t.category)}" ${filter?`data-tag="${esc(t.name)}"`:''}>${esc(t.name)}</${filter?'button':'span'}>`; }
function wave(samples) {
  if (!samples?.length) return '';
  const points=samples.filter((_,i)=>i%Math.max(1,Math.floor(samples.length/75))===0).slice(0,90);
  const max=Math.max(...points.map(Math.abs),.01);
  return `<svg class="wave" viewBox="0 0 600 65" preserveAspectRatio="none" aria-hidden="true">${points.map((v,i)=>`<rect x="${i*600/points.length}" y="${32-Math.max(2,Math.abs(v)/max*28)}" width="${Math.max(2,600/points.length-3)}" height="${Math.max(4,Math.abs(v)/max*56)}" rx="2" fill="currentColor"/>`).join('')}</svg>`;
}
function card(e) {
  const authors=e.participants?.filter(c=>c.role==='voice'||c.role==='writer').map(c=>c.title).join(' · ')|| (e.kind==='author'?'Страница участника':'');
  return `<a class="card" href="${entryHash(e.key)}"><div class="card-art">${e.cover?`<img loading="lazy" src="${esc(e.cover)}" alt="">`:`<span class="art-symbol">${e.kind==='audio'?'♫':e.kind==='script'?'≋':'◎'}</span>`}<span class="card-kind">${labels[e.kind]}</span>${e.kind==='audio'?wave(e.waveform):''}${e.duration?`<span class="duration">${duration(e.duration)}</span>`:''}</div><div class="card-body"><h3>${esc(e.title)}</h3><div class="card-author">${esc(authors)}</div><p class="card-description">${esc(e.preview|| (e.kind==='author'?'Сохранённый участник библиотеки':'Описание отсутствует'))}</p><div class="card-tags">${(e.tags||[]).slice(0,5).map(t=>tagHtml(t)).join('')}</div><div class="card-bottom"><span class="status-dot ${e.state==='complete'?'complete':''}"></span>${stateLabels[e.state]||esc(e.state)}<span class="arrow">↗</span></div></div></a>`;
}
function heading(title,subtitle,eyebrow='ЛИЧНАЯ БИБЛИОТЕКА') { return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div></div>`; }
function counts() { Object.entries(labels).forEach(([k])=>{ $(`[data-count="${k}"]`).textContent=info.counts[k]||0; }); }
async function bootstrap() { info=await api('/api/bootstrap');token=info.token;counts(); }

async function showLibrary(kind) {
  listState={kind,q:'',tag:pendingTag,exclude:'',state:'',offset:0,items:[]};
  pendingTag='';
  const subtitles={audio:'То, что хочется сохранить и переслушать.',script:'Сценарии, которые остаются доступными без сайта.',author:'Голоса, авторы текстов и другие участники ваших сохранённых работ.'};
  $('#main').innerHTML=heading(labels[kind],subtitles[kind])+`<div class="filters"><input id="search" class="search" type="search" placeholder="Поиск по названию, тексту и участникам…" aria-label="Поиск"><select id="tag-filter" aria-label="Тег"><option value="">Все теги</option>${info.tags.map(t=>`<option value="${esc(t.name)}">${esc(t.name)} · ${esc(t.category)}</option>`).join('')}</select><select id="state-filter" aria-label="Состояние сохранения"><option value="">Все состояния</option><option value="complete">Всё сохранено</option><option value="metadata">Только текст</option><option value="partial">Сохранено частично</option></select></div><div class="filters"><select id="exclude-filter" aria-label="Исключить тег"><option value="">Не исключать теги</option>${info.tags.map(t=>`<option value="${esc(t.name)}">Исключить: ${esc(t.name)}</option>`).join('')}</select></div><div id="results-label" class="result-line"></div><div id="cards" class="grid"></div><button id="load-more" class="button ghost load-more" hidden>Показать ещё</button>`;
  $('#tag-filter').value=listState.tag;
  let debounce;
  $('#search').addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{listState.q=$('#search')?.value||'';loadList(true);},220);});
  for(const [id,key] of [['tag-filter','tag'],['exclude-filter','exclude'],['state-filter','state']]) $("#"+id).addEventListener('change',()=>{listState[key]=$("#"+id).value;loadList(true);});
  $('#load-more').addEventListener('click',()=>loadList(false));
  await loadList(true);
}
let listRequest=0;
async function loadList(reset=true) {
  if(!$('#cards'))return;
  const request=++listRequest;
  const s=listState,offset=reset?0:s.items.length;
  const params=new URLSearchParams({kind:s.kind,q:s.q,offset:String(offset)});
  for(const k of ['tag','exclude','state'])if(s[k])params.set(k,s[k]);
  try {
    const data=await api('/api/library?'+params);
    if(request!==listRequest||!$('#cards'))return;
    s.items=reset?data.items:s.items.concat(data.items);
    $('#cards').innerHTML=s.items.map(card).join('');
    $('#results-label').textContent=`${s.kind==='author'?'Участников':'Работ'} в этом разделе: ${data.total}`;
    if(!s.items.length)$('#cards').innerHTML=`<div class="empty"><div class="symbol">${s.kind==='audio'?'♫':s.kind==='script'?'≋':'◎'}</div><h2>${s.q||s.tag||s.exclude||s.state?'Ничего не найдено':'Здесь начнётся ваша библиотека'}</h2><p>${s.q||s.tag||s.exclude||s.state?'Попробуйте другой запрос или уберите фильтры.':'Добавьте ссылки с Audiochan или загрузите TXT, собранный userscript. Сначала сохраним текст, затем — файлы.'}</p><a class="button" href="#import">＋ Добавить ссылки</a></div>`;
    $('#load-more').hidden=s.items.length>=data.total;
  }catch(e){toast(e.message);}
}
async function showEntry(key) {
  const turn=generation;
  const e=await api('/api/entry?key='+encodeURIComponent(key));
  if(turn!==generation)return;
  const participants=(e.participants||[]).map(c=>`<a class="participant" href="${entryHash(c.key)}"><small>${esc(c.role)}</small><span>${esc(c.title)} ↗</span></a>`).join('');
  const profile=e.kind==='author';
  const missingVersions=(e.expected_media||[]).filter(version=>!e.media.some(file=>file.id===version.id));
  const actions=`<div class="detail-actions">${e.media.map((m,i)=>`<button class="button ${i?'secondary':''}" data-play="${i}">▶ ${esc(m.label||'Слушать')}</button>`).join('')}${e.source_url?'<button id="fetch-media" class="button secondary">↓ Докачать файлы</button>':''}<a class="button ghost" href="${esc(e.offline_url)}" target="_blank" rel="noopener">Автономная страница ↗</a>${e.source_url?`<a class="button ghost" href="${esc(e.source_url)}" target="_blank" rel="noreferrer">Источник ↗</a>`:''}</div>`;
  $('#main').innerHTML=`<a class="back" href="#${e.kind}">← ${labels[e.kind]}</a><header class="detail-head"><div class="eyebrow">${profile?'УЧАСТНИК БИБЛИОТЕКИ':e.kind==='script'?'СЦЕНАРИЙ':'СОХРАНЁННОЕ АУДИО'}</div>${profile?`<div class="profile-header">${e.cover?`<img class="profile-avatar" src="${esc(e.cover)}" alt="">`:`<div class="profile-fallback">${esc(e.title.slice(0,1))}</div>`}<div><h1>${esc(e.title)}</h1><p class="subtitle">${esc(e.slug?'u/'+e.slug:'Имя из подписи к работе')}</p></div></div>`:`<h1>${esc(e.title)}</h1>`}<div class="tag-list">${e.tags.map(t=>tagHtml(t,true)).join('')}</div><p class="subtitle"><span class="status-dot ${e.state==='complete'?'complete':''}"></span> ${stateLabels[e.state]}${e.duration?' · '+duration(e.duration):''} · сохранено ${new Date(e.saved_at).toLocaleDateString('ru-RU')}</p>${actions}</header>${e.error?`<div class="notice">${esc(e.error)}</div>`:''}${e.kind==='audio'&&!e.media.length?'<div class="notice">Карточка уже на диске. Аудио ещё не сохранено — его можно докачать отдельно.</div>':''}${profile&&!e.linked_profile?'<div class="notice">Это имя из подписи. Профиль на Audiochan для него не подтверждён.</div>':''}<div class="detail-layout"><article class="document">${e.kind==='audio'&&e.waveform?.length?`<div class="wave-panel">${wave(e.waveform)}</div>`:''}${e.description_html}${e.body_html?'<hr>'+e.body_html:''}${e.notes_html?'<hr><h2>Примечания автора</h2>'+e.notes_html:''}${profile?`<div>${e.profile_links.map(l=>`<a class="author-link" href="${esc(l.url)}" target="_blank" rel="noreferrer">${esc(l.title||l.url)} ↗</a>`).join('')}</div><div id="author-works"></div>`:''}</article><aside class="detail-aside">${participants?'<div class="aside-heading">УЧАСТНИКИ</div>'+participants:''}<div class="file-info"><div class="aside-heading">НА ДИСКЕ</div><p>${esc(e.folder_label)}</p>${e.media.map(m=>`<a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.path.split('/').at(-1))} ↗</a><div>${(m.bytes/1048576).toFixed(1)} МБ · ${duration(m.duration)}</div><details><summary>SHA-256</summary><code>${esc(m.sha256)}</code></details>`).join('')}<a href="${esc('/files/'+e.folder.split('/').map(encodeURIComponent).join('/')+'/README.md')}">Описание Markdown ↗</a></div></aside></div>`;
  if(missingVersions.length)$('#main').insertAdjacentHTML('beforeend',`<div class="notice">Ожидают загрузки: ${missingVersions.map(v=>esc(v.label)).join(', ')}. Используйте «Докачать файлы», чтобы сохранить все версии.</div>`);
  document.querySelectorAll('[data-play]').forEach(b=>b.addEventListener('click',()=>play(e,Number(b.dataset.play))));
  $('#fetch-media')?.addEventListener('click',async()=>{try{await api('/api/entry-action',{key:e.key});toast('Поставлено в очередь докачивания');}catch(error){toast(error.message);}});
  document.querySelectorAll('[data-tag]').forEach(b=>b.addEventListener('click',()=>{pendingTag=b.dataset.tag;location.hash=e.kind==='script'?'script':'audio';}));
  if(profile){
    const [audios,scripts]=await Promise.all(['audio','script'].map(kind=>api('/api/library?'+new URLSearchParams({kind,person:e.key}))));
    if(turn!==generation||!$('#author-works'))return;
    const collections={audio:audios,script:scripts};
    $('#author-works').innerHTML=Object.entries(collections).map(([kind,data])=>`<h2>${labels[kind]} · ${data.total}</h2><div class="grid" data-works="${kind}">${data.items.map(card).join('')||'<p class="muted">Пока нет сохранённых работ этого типа.</p>'}</div><button class="button ghost load-more" data-more-works="${kind}" ${data.items.length>=data.total?'hidden':''}>Показать ещё</button>`).join('');
    document.querySelectorAll('[data-more-works]').forEach(button=>button.addEventListener('click',async()=>{
      const kind=button.dataset.moreWorks,data=collections[kind];button.disabled=true;
      try{const more=await api('/api/library?'+new URLSearchParams({kind,person:e.key,offset:String(data.items.length)}));if(turn!==generation)return;
        document.querySelector(`[data-works="${kind}"]`).insertAdjacentHTML('beforeend',more.items.map(card).join(''));data.items.push(...more.items);button.hidden=data.items.length>=more.total;
      }catch(error){toast(error.message);}finally{button.disabled=false;}
    }));
  }
}
function showImport() {
  $('#main').innerHTML=heading('Добавить в библиотеку','Одна ссылка или целый список. Дальше архиватор разберётся сам.','СОХРАНЕНИЕ')+`<div class="import-layout"><section class="box"><h2>Ссылки с Audiochan</h2><p>Аудио <code>/a/…</code>, сценарии <code>/s/…</code> и авторы <code>/u/…</code>. Каждая ссылка с новой строки.</p><textarea id="urls" class="url-input" placeholder="https://audiochan.com/a/…&#10;https://audiochan.com/s/…&#10;https://audiochan.com/u/…" aria-label="Список ссылок" spellcheck="false"></textarea><label class="check"><input id="with-media" type="checkbox" checked>После текста скачать аудио и иллюстрации</label><div class="import-actions"><button class="button" id="start-import">↓ Сохранить в библиотеку</button><label class="button secondary">Выбрать TXT<input id="txt-file" type="file" accept=".txt,text/plain" hidden></label></div><p id="import-result" role="status"></p></section><aside class="box"><h2>Всё остаётся у вас</h2><div class="step"><span class="step-number">1</span><div><strong>Сначала — текст</strong><p>Карточка, теги, авторы и сценарий сразу сохраняются на диск.</p></div></div><div class="step"><span class="step-number">2</span><div><strong>Затем — файлы</strong><p>Аудио и иллюстрации скачиваются отдельно. Ошибка не стирает карточку.</p></div></div><div class="step"><span class="step-number">3</span><div><strong>Независимо от сайта</strong><p>Обычные медиафайлы, JSON, Markdown и автономные страницы.</p></div></div><p>Ссылка автора сохраняет его профиль. Все работы автора автоматически не скачиваются.</p></aside></div>`;
  $('#txt-file').addEventListener('change',async event=>{const file=event.target.files[0];if(file)$('#urls').value=await file.text();});
  $('#start-import').addEventListener('click',async()=>{
    const button=$('#start-import');button.disabled=true;
    try{const result=await api('/api/import',{text:$('#urls').value,media:$('#with-media').checked});$('#import-result').textContent=`Добавлено: ${result.added}. Уже в очереди: ${result.duplicates}.`;
      toast(result.added?'Ссылки добавлены. Сохранение уже началось.':'Новых ссылок нет. Проверьте адреса или откройте загрузки.');
    }catch(e){toast(e.message);}finally{button.disabled=false;}
  });
}
async function showQueue() {
  $('#main').innerHTML=heading('Загрузки','Карточки сохраняются раньше медиа. Незавершённые задачи остаются в очереди.','СОХРАНЕНИЕ')+`<div class="queue-controls"><button class="button secondary" data-queue="retry">↻ Повторить ошибки</button><button class="button ghost" data-queue="pause">Пауза после текущей работы</button><button class="button ghost" data-queue="resume">Продолжить</button><a href="/api/export" class="button ghost">Экспорт URL</a><button id="rebuild" class="button ghost">Восстановить индекс</button></div><p class="result-line" id="queue-label"></p><div id="queue-items"></div>`;
  queueOffset=0;
  $('#queue-items').insertAdjacentHTML('afterend','<div class="queue-controls"><button class="button ghost" id="queue-prev">← Новее</button><button class="button ghost" id="queue-next">Старее →</button></div>');
  $('#queue-prev').addEventListener('click',()=>{queueOffset=Math.max(0,queueOffset-100);pollQueue().catch(e=>toast(e.message));});
  $('#queue-next').addEventListener('click',()=>{queueOffset+=100;pollQueue().catch(e=>toast(e.message));});
  document.querySelectorAll('[data-queue]').forEach(b=>b.addEventListener('click',()=>queueAction(b.dataset.queue)));
  $('#rebuild').addEventListener('click',async()=>{try{await api('/api/rebuild',{});await bootstrap();toast('Индекс и автономные страницы восстановлены');}catch(e){toast(e.message);}});
  await pollQueue();
}
async function queueAction(action,id) {try{await api('/api/queue',{action,id});await pollQueue();}catch(e){toast(e.message);}}
async function pollQueue() {
  const data=await api('/api/queue?'+($('#queue-items')?'offset='+queueOffset:'summary=1'));
  $('#queue-count').textContent=data.pending;
  const active=data.active;
  $('#activity').textContent=data.paused?'Очередь на паузе':active?active.progress||'Сохранение…':data.pending?`В очереди: ${data.pending}`:'Локальная библиотека';
  if($('#queue-items')) {
    $('#queue-label').textContent=`Всего: ${data.total} · В работе: ${data.pending} · Показаны ${data.items.length} задач${data.paused?' · Пауза':''}`;
    $('#queue-prev').disabled=queueOffset===0;
    $('#queue-next').disabled=queueOffset+data.items.length>=data.total;
    $('#queue-items').innerHTML=data.items.slice().reverse().map(t=>`<div class="queue-item"><span class="task-icon">${t.kind==='author'?'◎':t.kind==='script'?'≡':'♫'}</span><div class="task-main"><h3>${t.entry_key?`<a href="${entryHash(t.entry_key)}">${esc(t.title)}</a>`:esc(t.title)}</h3><a class="task-url" href="${esc(t.url)}" target="_blank" rel="noreferrer">${esc(t.url)}</a><div class="task-state">${taskLabels[t.stage]}${t.progress?' · '+esc(t.progress):''}</div>${t.error?`<div class="task-error">${esc(t.error)}</div>`:''}</div><div class="task-buttons">${!['reading','downloading'].includes(t.stage)?`<button class="button ghost" data-task="${esc(t.id)}" data-action="refresh">Обновить текст</button>${t.entry_key?`<button class="button secondary" data-task="${esc(t.id)}" data-action="media">Докачать файлы</button>`:''}${t.error?`<button class="button secondary" data-task="${esc(t.id)}" data-action="retry">Повторить</button>`:''}`:''}</div></div>`).join('')||'<div class="empty"><h2>Очередь пока пуста</h2><a href="#import" class="button">Добавить ссылки</a></div>';
    document.querySelectorAll('[data-task]').forEach(b=>b.addEventListener('click',()=>queueAction(b.dataset.action,b.dataset.task)));
  }
  const signature=data.signature;
  if(lastQueueSignature&&lastQueueSignature!==signature){await bootstrap();if(['audio','script','author'].includes(location.hash.slice(1)||'audio'))await loadList(true);}
  lastQueueSignature=signature;
}
async function savePosition() {
  if(!track||audio.readyState<1||!Number.isFinite(audio.currentTime))return;
  const media=track.entry.media[track.index];
  if(audio.currentSrc!==new URL(media.url,location.href).href)return;
  const position=audio.currentTime;
  track.entry.positions??={};track.entry.positions[media.id]=position;
  try{await api('/api/position',{key:track.entry.key,file_id:media.id,position});}catch{}
}
async function play(entry,index=0) {
  await savePosition();
  const media=entry.media[index];if(!media)return;
  track={entry,index};lastPositionSave=0;
  $('#player').hidden=false;$('#player-title').textContent=entry.title+' · '+media.label;$('#player-title').href=entryHash(entry.key);$('#player-author').textContent=entry.participants.filter(c=>c.role==='voice').map(c=>c.title).join(' · ');
  audio.src=media.url;audio.playbackRate=Number($('#speed').value);
  const position=Number(entry.positions?.[media.id]??(index===0?entry.position:0))||0;
  audio.addEventListener('loadedmetadata',()=>{if(position<audio.duration-2)audio.currentTime=position;},{once:true});
  try{await audio.play();}catch{toast('Нажмите воспроизведение в плеере.');}
}
audio.addEventListener('timeupdate',()=>{if(Date.now()-lastPositionSave>5000){lastPositionSave=Date.now();savePosition();}});
audio.addEventListener('pause',savePosition);
audio.addEventListener('error',()=>toast('Не удалось воспроизвести локальный файл. Проверьте его наличие на диске.'));
$('#speed').addEventListener('change',()=>audio.playbackRate=Number($('#speed').value));
$('#back15').addEventListener('click',()=>audio.currentTime=Math.max(0,audio.currentTime-15));
$('#forward15').addEventListener('click',()=>{if(Number.isFinite(audio.duration))audio.currentTime=Math.min(audio.duration,audio.currentTime+15);});
$('#close-player').addEventListener('click',async()=>{audio.pause();await savePosition();audio.removeAttribute('src');audio.load();track=null;$('#player').hidden=true;});
window.addEventListener('pagehide',()=>{if(track&&audio.readyState>=1)fetch('/api/position',{method:'POST',headers:{'Content-Type':'application/json','X-Archive-Token':token},body:JSON.stringify({key:track.entry.key,file_id:track.entry.media[track.index].id,position:audio.currentTime||0}),keepalive:true});});
async function route() {
  generation++;listRequest++;
  const hash=location.hash.slice(1)||'audio';
  document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===hash));
  try{
    if(hash.startsWith('entry/'))await showEntry(decodeURIComponent(hash.slice(6)));
    else if(hash==='import')showImport();
    else if(hash==='queue')await showQueue();
    else await showLibrary(labels[hash]?hash:'audio');
  }catch(e){$('#main').innerHTML=heading('Не удалось открыть страницу',esc(e.message));}
  window.scrollTo(0,0);
}
window.addEventListener('hashchange',route);
(async()=>{try{await bootstrap();await route();await pollQueue();setInterval(()=>pollQueue().catch(()=>{$('#activity').textContent='Сервер недоступен';}),2000);}catch(e){$('#main').innerHTML=heading('Сервер недоступен',esc(e.message));}})();
