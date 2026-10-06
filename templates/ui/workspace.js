/* Sfera / CLIENT workspace. Every displayed record comes from the scoped API. */
const workspace={hidden:new Set(),hideKinds:new Set(),hideEdges:new Set(),query:'',node:null,page:null,metrics:null,metricsError:'',zoom:1,pan:[0,0]};
const PAGE_NAMES={overview:'Сфера',graph:'Граф',dashboards:'Дашборды',docs:'Конфлюенс',access:'Админка',profile:'Мой профиль'};
function workspaceShell(content){
 const identity=state.access?.identity||{};
 const name=identity.username||'Пользователь';
 const navigation=Object.entries(PAGE_NAMES).filter(([key])=>key!=='access'||state.access?.can_admin);
 return `<div class="shell"><aside class="sidebar"><div class="sidebar-logo"><span class="logo-symbol">A<span>·</span></span><span>Сфера<small>CLIENT</small></span></div><div class="side-caption">РАБОЧЕЕ ПРОСТРАНСТВО</div><nav aria-label="Основная навигация">${navigation.map(([key,label])=>nav(key,label)).join('')}</nav><div class="sidebar-bottom"><button class="profile-shortcut" data-view="profile"><span class="avatar">${escapeHtml(name.slice(0,2).toUpperCase())}</span><span><strong>${escapeHtml(name)}</strong><small>${identity.acl_admin?'Администратор':'Участник CLIENT'}</small></span></button><button class="nav-button logout" data-action="logout">${icon('logout')}<span>Выйти</span></button></div></aside><div class="main-column"><header class="topbar"><div class="breadcrumbs">CLIENT ${icon('chevron',13)} <strong>${PAGE_NAMES[state.view]||'Сфера'}</strong></div><div class="topbar-actions"><span class="sync-state"><i></i> Соединение защищено</span><button class="icon-button" data-action="refresh" aria-label="Обновить данные">${icon('refresh')}</button><button class="avatar" data-view="profile" aria-label="Открыть мой профиль">${escapeHtml(name.slice(0,2).toUpperCase())}</button></div></header><main class="content">${content}</main></div></div>`;
}
function number(value){return value==null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:1});}
function metricCard(title,value,note){return `<div class="metric-card"><div class="metric-top">${escapeHtml(title)}</div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(note)}</span></div>`;}
function galaxyHome(){
 let inner=cognitiveView();
 // Existing drill-down model is reused; the large map is the primary content.
 const start=inner.indexOf('<main class="content">')+22,end=inner.lastIndexOf('</main>');
 inner=inner.slice(start,end);
 const stats=`<div class="metrics galaxy-summary">${metricCard('Знания',number(state.health?.node_count),'доступных активных тезисов')}${metricCard('Страницы',number(state.health?.document_count),'канонических Markdown')}${metricCard('Связи',number(state.health?.edge_count),'сохранённых отношений')}${metricCard('Задачи',number(state.health?.task_count),'доступных задач')}</div>`;
 return shell(`${heading('Сфера / CLIENT','Сфера','Раскрывай области, разделы и отдельные знания. Состав ниже отражает доступные тебе материалы.',`<button class="outline-button" data-view="graph">Полный граф ${icon('arrow',16)}</button>`)}${stats}<div class="galaxy-drill">${inner}</div>`);
}
function fullGraphModel(){
 const items=new Map([...state.items,...state.documents].map(i=>[i.id,i]));
 const nodes=(state.graph?.nodes||[]).filter(n=>!workspace.hidden.has(n.area)&&!(workspace.hidden.has('world')&&worldTopic(items.get(n.id)||n))&&!workspace.hideKinds.has(n.kind==='source'?'source':n.kind==='area'?'area':n.kind==='document'?'document':n.kind==='task'?'task':n.kind==='skill'?'skill':'knowledge'));
 const baseIds=new Set(nodes.filter(n=>n.kind!=='source'&&n.kind!=='area').map(n=>n.id));
 const linked=new Set((state.graph?.edges||[]).filter(e=>baseIds.has(e.source_item_id)||baseIds.has(e.target_item_id)).flatMap(e=>[e.source_item_id,e.target_item_id]));
 const visible=nodes.filter(n=>!['source','area'].includes(n.kind)||linked.has(n.id));
 const ids=new Set(visible.map(n=>n.id));
 return {nodes:visible,edges:(state.graph?.edges||[]).filter(e=>ids.has(e.source_item_id)&&ids.has(e.target_item_id)&&!workspace.hideEdges.has(e.edge_kind))};
}
function fullGraphSvg(model){
 const positions=new Map(),groups=new Map();
 model.nodes.forEach(n=>{let group=n.area||n.kind; if(!groups.has(group))groups.set(group,[]);groups.get(group).push(n);});
 const keys=[...groups.keys()];
 keys.forEach((key,index)=>{const angle=index*2*Math.PI/keys.length,cx=700+440*Math.cos(angle),cy=400+245*Math.sin(angle);groups.get(key).forEach((n,i)=>{const r=12+8*Math.sqrt(i),a=i*2.399963;positions.set(n.id,[cx+r*Math.cos(a),cy+r*Math.sin(a)]);});});
 const color=n=>CORE_DEFS.find(c=>c.key===n.area)?.color||'#718294';
 const query=workspace.query.trim().toLocaleLowerCase('ru');
 const edges=model.edges.map(e=>{const a=positions.get(e.source_item_id),b=positions.get(e.target_item_id);return `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${e.edge_kind==='semantic'?'#7e8b96':'#c6ced4'}" stroke-opacity="${e.edge_kind==='semantic'?'.4':'.14'}" aria-hidden="true" stroke-width="${e.edge_kind==='semantic'?'.8':'.5'}" ${e.verification_status==='model_proposed'?'stroke-dasharray="3 3"':''}><title>${escapeHtml(e.relation_type)} · ${escapeHtml(e.verification_status||e.edge_kind)}</title></line>`;}).join('');
 const dots=model.nodes.map(n=>{const p=positions.get(n.id),match=query&&n.title.toLocaleLowerCase('ru').includes(query),chosen=n.id===workspace.node;
 return `<g data-full-node="${escapeHtml(n.id)}" transform="translate(${p[0]} ${p[1]})" tabindex="0" role="button" aria-label="${escapeHtml(n.title)}" class="full-node ${chosen?'chosen':''}"><title>${escapeHtml(n.title)} · ${escapeHtml(n.kind)}</title><circle r="${chosen||match?8:n.kind==='area'?10:4}" fill="${color(n)}" opacity="${query&&!match&&!chosen?'.2':'1'}" stroke="${chosen||match?'#be5147':'#fff'}" stroke-width="${chosen||match?3:.5}"/>${n.kind==='area'||chosen||match?`<text y="-13" text-anchor="middle" fill="#263541" font-size="12">${escapeHtml(short(n.title))}</text>`:''}</g>`;}).join('');
 return `<div class="full-map" id="full-map"><svg viewBox="0 0 1400 800" role="group" aria-label="Полный граф доступных элементов Сфера"><rect width="1400" height="800" fill="#fafbf9"/><g id="full-scene" transform="translate(${workspace.pan[0]} ${workspace.pan[1]}) scale(${workspace.zoom})">${edges}${dots}</g></svg></div>`;
}
function fullGraphView(){
 const model=fullGraphModel(),node=model.nodes.find(n=>n.id===workspace.node);
 const neighbors=node?model.edges.filter(e=>e.source_item_id===node.id||e.target_item_id===node.id):[];
 const filters=AREAS;
 const card=node?`<h2>${escapeHtml(node.title)}</h2><p>${escapeHtml(node.kind)} · ${escapeHtml(node.status||'')}</p>${node.kind==='document'?`<button class="outline-button" data-wiki-page="${escapeHtml(node.id.replace(/^document:/,''))}">Открыть страницу</button>`:state.items.some(i=>i.id===node.id)?`<button class="outline-button" data-item="${escapeHtml(node.id)}">Читать материал</button>`:''}<h3>Связи · ${neighbors.length}</h3>${neighbors.map(e=>{const other=model.nodes.find(n=>n.id===(e.source_item_id===node.id?e.target_item_id:e.source_item_id));return `<button class="graph-neighbor" data-full-node="${escapeHtml(other.id)}"><strong>${escapeHtml(other.title)}</strong><small>${escapeHtml(e.relation_type)} · ${escapeHtml(e.verification_status||e.edge_kind)}</small></button>`;}).join('')}`:'<h2>Исследуй связи</h2><p>Выбери узел, чтобы увидеть все его связи и перейти к материалу.</p><p>Сплошные линии — сохранённые отношения, пунктир — предложения модели. Светлые линии — принадлежность разделу и происхождение.</p>';
 return shell(`${heading('СВЯЗИ / CLIENT','Граф','Все доступные знания, страницы, задачи, навыки и источники. Фильтры меняют отображение, сохраняя данные.')}<div class="full-graph-layout"><section class="panel full-graph-panel"><div class="full-graph-toolbar"><label class="search-box">${icon('search')}<input id="full-search" aria-label="Найти в полном графе" placeholder="Найти узел…" value="${escapeHtml(workspace.query)}"></label><span id="full-count">${model.nodes.length} элементов · ${model.edges.length} связей</span><button class="icon-button" data-full-zoom="out" aria-label="Уменьшить граф">−</button><button class="icon-button" data-full-zoom="reset" aria-label="Вписать полный граф">↺</button><button class="icon-button" data-full-zoom="in" aria-label="Увеличить граф">+</button></div><div class="full-search-results">${workspace.query.trim()?model.nodes.filter(n=>n.title.toLocaleLowerCase('ru').includes(workspace.query.trim().toLocaleLowerCase('ru'))).slice(0,12).map(n=>`<button data-full-node="${escapeHtml(n.id)}">${escapeHtml(n.title)}</button>`).join('')||'<span>Совпадений нет</span>':''}</div><details class="graph-filters" open><summary>Отображаемые разделы и элементы</summary><div class="filter-chips">${filters.map(a=>`<label><input type="checkbox" data-full-area="${a.key}" ${workspace.hidden.has(a.key)?'':'checked'}>${a.title}</label>`).join('')}</div><div class="filter-chips">${Object.entries({knowledge:'Знания',document:'Страницы',task:'Задачи',skill:'Навыки',area:'Разделы',source:'Источники'}).map(([key,title])=>`<label><input type="checkbox" data-full-kind="${key}" ${workspace.hideKinds.has(key)?'':'checked'}>${title}</label>`).join('')}</div><div class="filter-chips">${Object.entries({semantic:'Отношения',membership:'Принадлежность разделу',provenance:'Происхождение',reference:'Ссылки страниц'}).map(([key,title])=>`<label><input type="checkbox" data-full-edge="${key}" ${workspace.hideEdges.has(key)?'':'checked'}>${title}</label>`).join('')}</div></details>${fullGraphSvg(model)}<p class="map-help">Колесо — масштаб · фон — перемещение · узел — все его связи. Здесь нет ограничения числа узлов.</p></section><aside class="panel full-inspector">${card}</aside></div>`);
}
async function loadMetrics(){
 workspace.metricsError='';
 try{workspace.metrics=await request('/v1/daughter/metrics');}catch(e){workspace.metrics=null;workspace.metricsError=e.message;}
 if(state.view==='dashboards')render();
}
function bytes(value){if(value==null)return '—';const units=['Б','КБ','МБ','ГБ','ТБ'];let v=Number(value),i=0;while(v>=1024&&i<units.length-1){v/=1024;i++;}return number(v)+' '+units[i];}
function sparkline(history,key){const data=history.map(s=>Number(s[key])).filter(Number.isFinite);if(data.length<2)return '<p class="muted">История появится после двух замеров.</p>';const min=Math.min(...data),max=Math.max(...data),points=data.map((v,i)=>`${i*500/(data.length-1)},${100-(v-min)*90/(max-min||1)}`).join(' ');return `<svg class="metric-spark" viewBox="0 0 500 110" role="img" aria-label="История ${escapeHtml(key)}"><polyline points="${points}" fill="none" stroke="#b94b40" stroke-width="2"/></svg>`;}
function dashboardsView(){
 if(!state.access?.identity?.acl_admin)return shell(`${heading('СИСТЕМА','Дашборды','Общесистемные метрики доступны администратору Сферы.')}<section class="panel acl-panel"><p>Твой профиль не разрешает просмотр нагрузки и общих счётчиков. Состав доступных знаний показан на странице «Сфера».</p></section>`);
 const m=workspace.metrics;
 if(!m)return shell(`${heading('МОНИТОРИНГ','Дашборды','Состояние сервисов, запросы, токены и объём хранилищ.')}<section class="panel acl-panel">${escapeHtml(workspace.metricsError||'Загрузка фактических метрик…')}<button class="outline-button" data-metrics-refresh>Обновить</button></section>`);
 const counts=m.counters||{},ui=counts.galactica_ui_api||{},mcp=counts.galactica_client_mcp||{},model=counts.galactica_autonomy_protocol||{};
 const sampled=new Date(m.sampled_at*1000),stale=Date.now()/1000-m.sampled_at>180;
 return shell(`${heading('МОНИТОРИНГ / CLIENT','Дашборды',`Замер ${sampled.toLocaleString('ru-RU')} · обновление каждую минуту${stale?' · данные устарели':''}`,`<button class="outline-button" data-metrics-refresh>Обновить</button>`)}<div class="metrics">${metricCard('Запросы сайта',number(ui.requests),'HTTP, включая проверки доступа')}${metricCard('Запросы MCP',number(mcp.requests),'HTTP на /mcp, включая отказы')}${metricCard('Токены локальной модели',model.model_calls?number((model.input_tokens||0)+(model.output_tokens||0)):'Нет замеров','обработка итогов; только полученный usage')}${metricCard('Вызовы локальной модели',number(model.model_calls),'с начала сбора метрик')}</div><div class="dashboard-grid"><section class="panel acl-panel"><h2>Запросы и ошибки</h2><p>Сбор начат ${new Date(m.started_at*1000).toLocaleString('ru-RU')}. Счётчики сохраняются между перезапусками; история до включения сбора отсутствует.</p><table class="system-table"><thead><tr><th>Канал</th><th>Запросы</th><th>HTTP ошибки</th><th>Среднее время</th></tr></thead><tbody>${[['Сайт',ui],['MCP',mcp]].map(([label,c])=>`<tr><td>${label}</td><td>${number(c.requests)}</td><td>${number(c.errors)}</td><td>${c.requests?number(c.duration_ms/c.requests)+' мс':'—'}</td></tr>`).join('')}</tbody></table>${sparkline(m.history||[],'requests')}<h3>Токены</h3><p>Вход: ${number(model.input_tokens)} · выход: ${number(model.output_tokens)} · вызовов без usage: ${number(model.usage_missing)}.</p><p class="muted">Учитывается локальная модель обработки итогов Сфера. Расход токенов и стоимость личных Codex/Claude неизвестны серверу. Цена локальной модели не настроена; денежный расход не рассчитывается.</p></section><section class="panel acl-panel"><h2>Хранилища</h2><table class="system-table"><thead><tr><th>База</th><th>Объём</th><th>Измерение</th></tr></thead><tbody>${(m.databases||[]).map(d=>`<tr><td>${escapeHtml(d.engine)}</td><td>${bytes(d.bytes)}</td><td>${d.engine==='PostgreSQL'?'pg_database_size':'каталог данных на диске'}</td></tr>`).join('')}</tbody></table>${sparkline(m.history||[],'postgres_bytes')}<p>PostgreSQL: соединений ${number(m.databases?.find(d=>d.engine==='PostgreSQL')?.connections)}, активных запросов ${number(m.databases?.find(d=>d.engine==='PostgreSQL')?.active_queries)}.</p><p class="muted">Объёмы относятся только к Сфера CLIENT. Нагрузка каждого сервиса показана ниже.</p></section></div><section class="panel acl-panel"><h2>Сервисы и нагрузка</h2><div class="table-scroll"><table class="system-table"><thead><tr><th>Сервис</th><th>Состояние</th><th>CPU</th><th>Память</th><th>Сеть</th><th>Диск I/O</th></tr></thead><tbody>${(m.services||[]).map(s=>`<tr><td>${escapeHtml(s.name.replace('galactica_',''))}</td><td><span class="service-state ${s.running?'up':'down'}">${s.running===true?'Работает':s.running===false?'Остановлен':'Нет замера'}</span></td><td>${escapeHtml(s.cpu||'—')}</td><td>${escapeHtml(s.memory||'—')}</td><td>${escapeHtml(s.network_io||'—')}</td><td>${escapeHtml(s.disk_io||'—')}</td></tr>`).join('')}</tbody></table></div><p class="muted">CPU и память — текущий замер Docker. Сеть и диск I/O — накопленные счётчики контейнера с его запуска.</p></section>`);
}
function markdownInline(text){
 const escaped=escapeHtml(text);
 return escaped.replace(/\[([^\]]+)\]\(([^)]+)\)/g,(whole,title,target)=>{
   if(/^https?:\/\//i.test(target))return `<a href="${target}" target="_blank" rel="noopener noreferrer">${title}</a>`;
   const current=state.documents.find(p=>p.id===workspace.page)?.content.relative_path||'';
   let parts=current.split('/').slice(0,-1);
   for(const part of target.split('#')[0].split('/')){if(part==='..')parts.pop();else if(part&&part!=='.')parts.push(part);}
   const page=state.documents.find(p=>p.content.relative_path===parts.join('/'));
   return page?`<button class="wiki-inline-link" data-wiki-page="${page.id}">${title}</button>`:whole;
 }).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
}
function markdownPage(body){
 const lines=String(body||'').replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/,'').split('\n');
 let code=false,table=false;const html=[];
 for(const line of lines){
  if(line.startsWith('```')){if(table){html.push('</tbody></table></div>');table=false;}code=!code;html.push(code?'<pre><code>':'</code></pre>');continue;}
  if(code){html.push(escapeHtml(line)+'\n');continue;}
  if(line.trim().startsWith('|')){if(!table){html.push('<div class="table-scroll"><table class="system-table"><tbody>');table=true;}if(/^\s*\|[\s:|\-]+\|\s*$/.test(line))continue;html.push('<tr>'+line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>'<td>'+markdownInline(cell.trim())+'</td>').join('')+'</tr>');continue;}
  if(table){html.push('</tbody></table></div>');table=false;}
  const h=/^(#{1,6})\s+(.+)$/.exec(line);
  if(h)html.push(`<h${h[1].length}>${markdownInline(h[2])}</h${h[1].length}>`);
  else if(/^[-*]\s/.test(line))html.push(`<div class="wiki-bullet">• ${markdownInline(line.slice(2))}</div>`);
  else if(line.trim())html.push(`<p>${markdownInline(line)}</p>`);
 }
 if(table)html.push('</tbody></table></div>');if(code)html.push('</code></pre>');return html.join('');
}
function confluenceView(){
 if(state.area!=='all'||state.query||state.filter!=='all')return docsView();
 const pages=[...state.documents].sort((a,b)=>a.content.relative_path.localeCompare(b.content.relative_path));
 const page=pages.find(p=>p.id===workspace.page)||pages.find(p=>p.content.relative_path==='GALACTICA.md')||pages[0];
 workspace.page=page?.id;
 return shell(`${heading('БАЗА ЗНАНИЙ','Конфлюенс','Страницы, методики и знания Сфера. Доступ к материалам определяется твоими правами.')}<div class="wiki-layout"><aside class="panel wiki-tree"><h3>Дерево страниц</h3>${pages.map(p=>{const path=p.content.relative_path;return `<button class="wiki-tree-page ${p.id===page?.id?'selected':''}" data-wiki-page="${p.id}" style="padding-left:${12+Math.min(4,path.split('/').length-1)*12}px"><small>${escapeHtml(path.includes('/')?path.split('/').slice(0,-1).join('/'):'Сфера')}</small><strong>${escapeHtml(p.title)}</strong></button>`;}).join('')}<h3>Все знания</h3><button class="outline-button" data-knowledge-all>Открыть поиск материалов</button>${AREAS.map(a=>navArea(a)).join('')}</aside><article class="panel wiki-article">${page?`<div class="wiki-meta">${escapeHtml(page.content.relative_path)} · версия ${page.content.version} · ${escapeHtml(new Date(page.content.updated_at).toLocaleString('ru-RU'))}</div><div class="markdown-page">${markdownPage(page.content.body)}</div>`:'<p>Нет доступных страниц.</p>'}</article></div>`);
}
function profileView(){
 const id=state.access?.identity||{},role=id.acl_admin?'Администратор Сферы':{manager:'Редактор',contributor:'Участник',reader:'Читатель',owner:'Администратор'}[id.role]||id.role||'Участник';
 return shell(`${heading('УЧЁТНАЯ ЗАПИСЬ','Мой профиль','Текущая подтверждённая личность и разрешения. Вход самостоятельный, через этот экземпляр Сферы.')}<div class="profile-grid"><section class="panel acl-panel"><div class="profile-hero"><span class="profile-avatar">${escapeHtml((id.username||'TT').slice(0,2).toUpperCase())}</span><div><h2>${escapeHtml(id.username||'Пользователь')}</h2><p>${escapeHtml(role)}</p></div></div><dl class="profile-facts"><dt>Контур</dt><dd>CLIENT</dd><dt>Источник входа</dt><dd>Сфера</dd><dt>ID пользователя Сфера</dt><dd>${escapeHtml(id.source_subject_key)}</dd><dt>Сессия действует до</dt><dd>${id.session_expires_at?escapeHtml(new Date(Number(id.session_expires_at)*1000).toLocaleString('ru-RU')):'Срок не передан источником'}</dd><dt>Управление доступом</dt><dd>${state.access?.can_admin?'Доступно':'Не выдано'}</dd></dl></section><section class="panel acl-panel"><h2>Доступ к разделам</h2><ul class="profile-sections">${(state.access?.sections||[]).map(s=>`<li><span>${escapeHtml(s.title)}</span><strong>${s.read?'Чтение разрешено':'Чтение закрыто'}</strong></li>`).join('')}</ul><p class="muted">Разрешения проверяются при каждом запросе. Права отдельного материала могут уточнять права раздела. Для отзыва доступа администратор меняет разрешения в Сфера.</p><a class="outline-button" href="/galactica/INSTALL_RU.md" target="_blank" rel="noopener">Подключить Codex или Claude</a></section></div>`);
}
function bindWorkspace(){
 for(const [attribute,set] of [['area',workspace.hidden],['kind',workspace.hideKinds],['edge',workspace.hideEdges]])app.querySelectorAll(`[data-full-${attribute}]`).forEach(el=>el.addEventListener('change',()=>{const key=el.dataset['full'+attribute[0].toUpperCase()+attribute.slice(1)];el.checked?set.delete(key):set.add(key);workspace.node=null;render();}));
 const search=document.getElementById('full-search');if(search)search.addEventListener('input',e=>{const pos=e.target.selectionStart;workspace.query=e.target.value;render();const next=document.getElementById('full-search');next.focus();next.setSelectionRange(pos,pos);});
 app.querySelectorAll('[data-full-node]').forEach(el=>{const select=()=>{workspace.node=el.dataset.fullNode;render();};el.addEventListener('click',select);el.addEventListener('keydown',e=>{if(e.key==='Enter')select();});});
 app.querySelectorAll('[data-full-zoom]').forEach(el=>el.addEventListener('click',()=>{if(el.dataset.fullZoom==='reset'){workspace.zoom=1;workspace.pan=[0,0];}else{const old=workspace.zoom;workspace.zoom=Math.min(8,Math.max(.25,old*(el.dataset.fullZoom==='in'?1.3:1/1.3)));workspace.pan=workspace.pan.map((v,i)=>(i?400:700)-((i?400:700)-v)*workspace.zoom/old);}render();}));
 app.querySelectorAll('[data-wiki-page]').forEach(el=>el.addEventListener('click',()=>{workspace.page=el.dataset.wikiPage;state.view='docs';history.replaceState(null,'','#confluence');state.area='all';state.query='';state.filter='all';render();}));
 app.querySelector('[data-knowledge-all]')?.addEventListener('click',()=>{state.filter='knowledge-search';render();});
 app.querySelectorAll('[data-metrics-refresh]').forEach(el=>el.addEventListener('click',loadMetrics));
 const map=document.getElementById('full-map');if(map){const svg=map.querySelector('svg'),scene=svg.querySelector('#full-scene');let drag;
 const point=e=>{const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());};
 const paint=()=>scene.setAttribute('transform',`translate(${workspace.pan[0]} ${workspace.pan[1]}) scale(${workspace.zoom})`);
 map.addEventListener('wheel',e=>{e.preventDefault();const p=point(e),old=workspace.zoom;workspace.zoom=Math.min(8,Math.max(.25,old*(e.deltaY<0?1.1:1/1.1)));workspace.pan=[p.x-(p.x-workspace.pan[0])*workspace.zoom/old,p.y-(p.y-workspace.pan[1])*workspace.zoom/old];paint();},{passive:false});
 map.addEventListener('pointerdown',e=>{if(e.target.closest('[data-full-node]'))return;const p=point(e);drag=[p.x,p.y,...workspace.pan];map.setPointerCapture(e.pointerId);});
 map.addEventListener('pointermove',e=>{if(!drag)return;const p=point(e);workspace.pan=[drag[2]+p.x-drag[0],drag[3]+p.y-drag[1]];paint();});map.addEventListener('pointerup',()=>drag=null);map.addEventListener('pointercancel',()=>drag=null);
 }
}
