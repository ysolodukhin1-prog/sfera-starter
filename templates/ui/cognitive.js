/* Cognitive navigation is a UI projection of canonical areas and provenance.
 * Only edge_kind=semantic represents a stored relation. No relations are inferred here.
 */
const CORE_DEFS = [
  {key:'self', title:'Я', subtitle:'Правила и устройство', color:'#526777', mark:'Я'},
  {key:'knowledge', title:'Знания', subtitle:'Методы и предметная база', color:'#26778e', mark:'З'},
  {key:'skills', title:'Навыки', subtitle:'Процедуры и кандидаты', color:'#7760a0', mark:'Н'},
  {key:'experts', title:'Эксперты', subtitle:'Профили компетенций', color:'#b47c38', mark:'Э'},
  {key:'threads', title:'Опыт задач', subtitle:'Выводы из Codex', color:'#3a8474', mark:'О'},
  {key:'work', title:'Работа CLIENT', subtitle:'Требования и решения', color:'#be5147', mark:'Т'},
  {key:'world', title:'Мир', subtitle:'Базы, сервисы и подключения', color:'#728361', mark:'М'},
  {key:'user', title:'Пользователь', subtitle:'Предпочтения и намерения', color:'#9e6580', mark:'П'},
];
const KNOWLEDGE_TOPICS = [
  {key:'api',title:'API маркетплейсов',color:'#26778e',project:'679e276e-69bc-4c8c-9c54-fb10699f7f52'},
  {key:'metrics',title:'Метрики и аналитика',color:'#3a8474',project:'594240fa-6c2a-4234-8c84-b7fe01859258'},
  {key:'content',title:'Контент и дизайн',color:'#9e6580',project:'840f6494-accc-5a59-ace8-63d654e3ed0a'},
  {key:'ozon',title:'Продвижение Ozon',color:'#7760a0',project:'b2b19bde-914d-46c1-8fbc-e78e1ee3545e'},
  {key:'wb',title:'Ранжирование WB',color:'#b47c38',project:'d91e3dd2-1776-46ca-b176-f89360899a74'},
  {key:'trend',title:'Дизайн-система Сфера',color:'#be5147',system:'trend_runtime'},
  {key:'1c',title:'Данные 1С',color:'#728361',message:'direct:1c_database_structure_file_20260925'},
  {key:'other',title:'Другие знания',color:'#526777'},
];
// Reviewed cross references: they retain their primary area, ID and source status.
const WORLD_REFERENCES = {
  '647a5263-e54e-502b-af30-a31f04654620':'databases',
  '7f25fb4a-9fd2-51ea-9509-5c65407619a3':'runtime',
  '113dc8ae-0c0e-5faa-ba1e-96fe3f84a92d':'runtime',
  '1d19286b-0609-501c-93b1-915af20ef046':'access',
  '843b0292-9825-5bc1-a052-389be753bf0b':'access',
  '30a29df9-4976-5899-802c-ff3c8777272f':'access',
};
function worldTopic(item) {
  const content=item.content||item, spans=content.source_spans||[];
  if(WORLD_REFERENCES[item.id])return WORLD_REFERENCES[item.id];
  if((content.source_message_ids||[]).includes('direct:1c_database_structure_file_20260925') || spans.some(s=>s.message_id==='direct:1c_database_structure_file_20260925'))return 'databases';
  if((content.area||item.area)==='knowledge' && spans.some(s=>s.source_project_id==='679e276e-69bc-4c8c-9c54-fb10699f7f52'))return 'connections';
  if(content.relative_path==='CLIENT/memory/MPSTATS_API.md')return 'connections';
  if(['HARNESS.md','CLIENT/runtime/GALACTICA.md'].includes(content.relative_path))return 'runtime';
  return null;
}
const WORK_TOPICS = [
  {key:'requirements',title:'Требования и решения',color:'#be5147'},
  {key:'data',title:'Данные и отчётность',color:'#3a8474',pattern:/1[сc]|розниц|_accum|erp|отч[её]т|planfact|план.?факт|warehouse/i},
  {key:'access',title:'Доступ и инфраструктура',color:'#728361',pattern:/vpn|доступ|авторизац|permission|access|туннел|firewall|маршрут|сесси|session/i},
  {key:'api',title:'API и интеграции',color:'#26778e',pattern:/mpstats|подключен|интеграц|seller|api|lamoda/i},
  {key:'interface',title:'Интерфейс и граф',color:'#9e6580',pattern:/граф|интерфейс|ядр|svg|cognitive|\bui\b|frontend|вкладк|caption|label/i},
  {key:'memory',title:'Память и автоматизация',color:'#7760a0',pattern:/galactica|памят|консолид|протокол|protocol|outcome|qdrant|neo4j|nomic|gemma|markdown|знани|ночно|direct|federation/i},
  {key:'other',title:'Прочие результаты',color:'#526777'},
  {key:'documents',title:'Документы контура',color:'#8c7964'},
];
function topicMembers(topic) { return [...topic.nodes,...(topic.documents||[]).map(d=>({id:`document:${d.id}`,title:d.content.relative_path,kind:'document'}))]; }
function topicsForCore(model,key) { return model.topics.filter(t=>t.core===key); }
function buildExtraTopics(cores,items) {
  const taskTitles=new Map();
  for(const item of items.values()) if(item.content.kind==='task_insight') {
    for(const span of item.content.source_spans||[]) if(span.task_id)taskTitles.set(span.task_id,item.title);
  }
  const work=cores.find(c=>c.key==='work');
  const workTopics=WORK_TOPICS.map(t=>({...t,id:`topic:work:${t.key}`,core:'work',kind:'topic',nodes:[],documents:[]}));
  for(const node of work.nodes) {
    const content=items.get(node.id)?.content||node;
    const taskContext=(content.source_spans||[]).map(s=>taskTitles.get(s.task_id)||'').join(' ');
    const text=[node.title,content.body||'',taskContext].join(' ');
    const topic=['requirement','constraint','decision','goal'].includes(node.kind) && node.status!=='agent_inference' ? workTopics[0]
      : workTopics.find(t=>t.pattern?.test(text)) || workTopics.find(t=>t.key==='other');
    topic.nodes.push(node);
  }
  workTopics.find(t=>t.key==='documents').documents=work.documents;
  const world=cores.find(c=>c.key==='world');
  const worldTopics=[['databases','Базы данных','#3a8474'],['connections','API и подключения','#26778e'],['runtime','Сервисы Сферы','#7760a0'],['access','Доступ и сеть','#b47c38']].map(([key,title,color])=>({id:`topic:world:${key}`,key,title,color,core:'world',kind:'topic',nodes:world.nodes.filter(n=>worldTopic(items.get(n.id)||n)===key),documents:world.documents.filter(d=>worldTopic(d)===key)}));
  return [...workTopics,...worldTopics].filter(t=>t.nodes.length+t.documents.length);
}
function cognitiveModel() {
  const knowledge = (state.graph?.nodes || []).filter(n => !['area','source','document','task','skill'].includes(n.kind));
  const items = new Map(state.items.map(item => [item.id,item]));
  const cores = CORE_DEFS.map(def => {
    const nodes = knowledge.filter(n => n.area === def.key || def.key==='world' && worldTopic(items.get(n.id)||n));
    const documents = state.documents.filter(n => areaOf(n) === def.key || def.key==='world' && worldTopic(n));
    const skills = def.key === 'skills' ? state.health?.skills || [] : [];
    return {...def,id:`core:${def.key}`,kind:'core',nodes,documents,skills,count:nodes.length+documents.length+skills.length};
  });
  const byId = new Map(knowledge.map(n => [n.id,n]));
  const relations = (state.graph?.edges || []).filter(e => e.edge_kind === 'semantic' && byId.has(e.source_item_id) && byId.has(e.target_item_id));
  const degree = new Map();
  relations.forEach(e => [e.source_item_id,e.target_item_id].forEach(id => degree.set(id,(degree.get(id)||0)+1)));
  const groups = new Map();
  relations.forEach(edge => {
    const a = byId.get(edge.source_item_id).area, b = byId.get(edge.target_item_id).area;
    if (a === b) return;
    const pair = [a,b].sort(), key = pair.join(':');
    if (!groups.has(key)) groups.set(key,{id:`aggregate:${key}`,a:`core:${pair[0]}`,b:`core:${pair[1]}`,edges:[]});
    groups.get(key).edges.push(edge);
  });
  const topics = KNOWLEDGE_TOPICS.map(topic => ({...topic,id:`topic:${topic.key}`,kind:'topic',core:'knowledge',nodes:[],documents:[]}));
  for (const node of knowledge.filter(n => n.area === 'knowledge')) {
    const content = items.get(node.id)?.content || node;
    const spans = content.source_spans || [];
    const topic = topics.find(t => t.project && spans.some(s => s.source_project_id === t.project)
      || t.system && spans.some(s => s.source_system === t.system)
      || t.message && ((content.source_message_ids || []).includes(t.message) || spans.some(s => s.message_id === t.message))) || topics.at(-1);
    topic.nodes.push(node);
  }
  const rank = (a,b) => {
    const score = n => (n.status === 'agent_inference' ? 0 : 1000) + (degree.get(n.id)||0);
    return score(b)-score(a) || a.title.localeCompare(b.title,'ru');
  };
  topics.push(...buildExtraTopics(cores,items));
  cores.forEach(c => c.nodes.sort(rank)); topics.forEach(t => {t.nodes.sort(rank);t.count=t.nodes.length+(t.documents||[]).length;});
  return {knowledge,cores,topics:topics.filter(t=>t.count),relations,groups:[...groups.values()],byId,degree};
}
function cognitiveHome() {
  state.core = null; state.topic = null; state.showAll = false; state.graphLevel = 'overview'; state.selected = null;
  state.graphPath = null; state.pathStart = null; state.corePage = 0; graphRuntime.key = null;
}
function openCore(id) {
  state.core = id.replace('core:',''); state.topic = null; state.showAll = false; state.graphLevel = 'core';
  state.selected = null; state.graphPath = null; state.pathStart = null; state.corePage = 0;
  graphRuntime.key = null; state.view = 'overview'; render();
}
function topicForNode(model,id) { return model.topics.find(t=>t.nodes.some(n=>n.id===id)); }
function cognitiveSelect(id) {
  if (id === 'system:sfera') { cognitiveHome(); render(); return true; }
  if (id.startsWith('core:')) { openCore(id); return true; }
  if (id.startsWith('topic:')) {
    state.core=cognitiveModel().topics.find(t=>t.id===id)?.core||'knowledge'; state.showAll=false; state.view='overview'; state.topic=id; state.graphLevel='core'; state.selected=null; state.corePage=0;
    graphRuntime.key=null; render(); return true;
  }
  if (id.startsWith('document:')) { state.selected=id.slice(9); showDetail(); return true; }
  if (id.startsWith('skill:')) { state.view='docs'; state.area='skills'; render(); return true; }
  return false;
}
function cognitiveView() {
  const model = cognitiveModel();
  const core = model.cores.find(c=>c.key===state.core);
  const topic = model.topics.find(t=>t.id===state.topic);
  const active = model.byId.get(state.selected);
  const nodeView = state.graphLevel === 'node' && active;
  const neighborCount = nodeView ? new Set(model.relations.filter(e=>e.source_item_id===active.id||e.target_item_id===active.id).map(e=>e.source_item_id===active.id?e.target_item_id:e.source_item_id)).size : 0;
  const proposed = model.relations.filter(e=>e.verification_status==='model_proposed').length;
  const expansionCount = nodeView ? neighborCount : topic ? topic.count : core?.count;
  return shell(`<div class="cognitive-page">${heading('Сфера / КОГНИТИВНАЯ КАРТА','У знаний есть структура.','От общего ядра — к методам, навыкам, экспертам и опыту CLIENT.',`<button class="outline-button" data-action="refresh">${icon('refresh',16)} Обновить</button>`)}
    <section class="panel cognitive-workspace graph-workspace">
      <div class="cognitive-toolbar"><div class="cognitive-crumb"><button data-cognitive-home>CLIENT</button>${core ? `<span>/</span><button data-core="${core.id}">${escapeHtml(core.title)}</button>` : '<span>/</span><strong>Все ядра</strong>'}${topic ? `<span>/</span><strong>${escapeHtml(topic.title)}</strong>` : ''}${nodeView ? '<span>/</span><strong>Связи знания</strong>' : ''}</div><div class="cognitive-tools"><button class="icon-button" data-zoom="out" title="Уменьшить" aria-label="Уменьшить масштаб">−</button><button class="icon-button" data-action="fit" title="Вписать карту" aria-label="Вписать карту">${icon('expand',16)}</button><button class="icon-button" data-zoom="in" title="Увеличить" aria-label="Увеличить масштаб">+</button></div></div>
      <div class="cognitive-stats"><span><i class="pulse-dot"></i> Знания: ${model.knowledge.length}</span><span>Документы: ${state.documents.length}</span><span>Отношения: ${model.relations.length} <small>· ${proposed} предложены моделью</small></span></div>
      <div class="hierarchy-actions"><span>${!core?'Нажмите ядро → раздел → знание':topic?'Нажмите карточку, чтобы раскрыть её отношения':'Нажмите подчинённое ядро, чтобы раскрыть его содержимое'}</span>${core?`<button class="outline-button" data-show-all>${state.showAll?'Свернуть карту':`Развернуть все · ${expansionCount}`}</button><button class="text-button" data-cognitive-home>К общему ядру</button>`:''}</div>
      ${core?.key==='world'?'<p class="world-reference-note">Ссылки на существующие записи об окружении. Статусы сохранены; это знания из источников, а не мониторинг сервисов.</p>':''}
      ${graphSvg(false)}
      ${neighborCount>8&&!state.showAll?`<div class="core-pagination map-pagination"><button class="outline-button" data-page="-1" ${(state.corePage||0)===0?'disabled':''}>←</button><span>Соседи ${(state.corePage||0)*8+1}–${Math.min(neighborCount,(state.corePage||0)*8+8)} из ${neighborCount}</span><button class="outline-button" data-page="1" ${(state.corePage||0)*8+8>=neighborCount?'disabled':''}>→</button></div>`:''}
      <div class="cognitive-map-foot"><span><i class="map-key structure"></i> Структура ядра</span><span><i class="map-key relation"></i> Сохранённые отношения</span><span class="map-gesture">Тяните узлы · масштабируйте · раскрывайте</span></div>
    </section>
    <div class="cognitive-below"><section class="panel cognitive-inspector">${nodeView ? `<div class="inspector-head"><p class="eyebrow">СВЯЗИ И СОДЕРЖАНИЕ</p><button class="text-button" ${topic?`data-topic="${escapeHtml(topic.id)}"`:`data-core="${escapeHtml(core.id)}"`}>Назад к ${topic?'разделу':'ядру'} ${icon('arrow',14)}</button></div>${graphNodeDetail(active)}` : cognitiveCoreDetail(model,core,topic)}</section>
    <aside class="panel cognitive-search-panel"><p class="eyebrow">НАЙТИ В БАЗЕ</p><div class="graph-search"><input id="graph-search-input" type="search" placeholder="Название знания…" aria-label="Поиск узла графа" value="${escapeHtml(state.graphQuery)}"><div id="graph-search-results" class="graph-search-results">${graphSearchResults(state.graph)}</div></div><h3>Ядра Сферы</h3><div class="core-directory">${model.cores.map(c=>`<button data-core="${c.id}" class="${core?.key===c.key?'active':''}"><i style="background:${c.color}"></i><span>${c.title}</span><strong>${c.count}</strong></button>`).join('')}</div><p class="cognitive-note">Ядра — навигационная модель базы. Линии отношений и их статусы взяты из PostgreSQL. Источник каждого знания доступен в его карточке.</p></aside></div></div>`);
}
function cognitiveCoreDetail(model,core,topic) {
  if (!core) return `<div class="inspector-head"><p class="eyebrow">ЦЕНТРАЛЬНОЕ ЯДРО / CLIENT</p><span class="kind-badge">8 областей</span></div><h2>Одна система. Несколько видов памяти.</h2><p class="muted">Нажмите на ядро, чтобы раскрыть его знания и реальные связи. «Я» хранит правила агента, «Пользователь» — явные предпочтения, «Опыт задач» — выводы из работы.</p><div class="topic-shortcuts">${topicsForCore(model,'knowledge').map(t=>`<button data-topic="${t.id}"><i style="background:${t.color}"></i>${escapeHtml(t.title)}<b>${t.nodes.length}</b>${icon('arrow',14)}</button>`).join('')}</div>`;
  const records = topic ? topic.nodes : core.nodes;
  const ids = new Set(records.map(n=>n.id));
  const related = model.relations.filter(e=>ids.has(e.source_item_id)||ids.has(e.target_item_id));
  const modelCount = records.filter(n=>n.status==='agent_inference').length;
  const visible = records.slice((state.corePage||0)*8,(state.corePage||0)*8+8);
  const connections = model.groups.filter(g=>g.a===core.id||g.b===core.id).map(g=>({core:model.cores.find(c=>c.id===(g.a===core.id?g.b:g.a)),edges:g.edges})).filter(g=>g.core);
  return `<div class="inspector-head"><p class="eyebrow">ЯДРО / ${escapeHtml(core.title)}</p><span class="kind-badge">${records.length} знаний · ${related.length} отношений</span></div><h2>${escapeHtml(topic?.title || core.subtitle)}</h2>${topicsForCore(model,core.key).length ? `<div class="topic-pills"><button data-core="${core.id}" class="${!topic?'active':''}">Все</button>${topicsForCore(model,core.key).map(t=>`<button data-topic="${t.id}" class="${topic?.id===t.id?'active':''}">${escapeHtml(t.title)} · ${t.count}</button>`).join('')}</div><p class="cognitive-note">${core.key==='work'?'Тематическая группировка по тексту записи и исходной задаче; исходные типы и статусы сохранены.':core.key==='world'?'Перекрёстные ссылки на знания об окружении. Записи остаются в исходных областях; это сведения из источников, а не мониторинг доступности.':'Разделы собраны по контексту источников импорта.'}</p>` : ''}${modelCount ? `<p class="cognitive-note">${modelCount} записей — выводы модели; их статус виден в каждой карточке.</p>` : ''}
    ${connections.length&&!topic?`<div class="core-connections"><strong>Связано с ядрами</strong>${connections.map(g=>`<button data-core="${g.core.id}"><i style="background:${g.core.color}"></i>${escapeHtml(g.core.title)}<b>${g.edges.length}</b><small>${g.edges.filter(e=>e.verification_status==='model_proposed').length} предложены моделью</small></button>`).join('')}</div>`:''}
    <div class="core-records">${visible.map(n=>`<button data-search-node="${n.id}"><span class="record-mark" style="color:${core.color}">${icon('docs',18)}</span><span><strong>${escapeHtml(n.title)}</strong><small>${n.status==='agent_inference'?'Предложение модели':cognitiveModelStatus(n.status)} · ${model.degree.get(n.id)||0} отношений</small></span>${icon('arrow',14)}</button>`).join('')}</div>
    ${records.length>8?`<div class="core-pagination"><button class="outline-button" data-page="-1" ${(state.corePage||0)===0?'disabled':''}>←</button><span>${(state.corePage||0)*8+1}–${Math.min(records.length,(state.corePage||0)*8+8)} из ${records.length}</span><button class="outline-button" data-page="1" ${(state.corePage||0)*8+8>=records.length?'disabled':''}>→</button></div>`:''}
    ${(topic?.documents||core.documents).map(doc=>`<button class="core-document" data-item="${doc.id}">${icon('docs',18)}<span>${escapeHtml(doc.content.relative_path)}</span>${icon('arrow',14)}</button>`).join('')}
    ${core.skills.map(s=>`<div class="core-document">${icon('overview',18)}<span>${escapeHtml(s.title)}<small>Активный навык · v${s.version}</small></span></div>`).join('')}
    ${!core.count?'<div class="empty-inline">В этом ядре пока нет записей. Оно оставлено в карте как отдельная область.</div>':''}`;
}
function cognitiveLayout(preview=false) {
  const model=cognitiveModel(), core=model.cores.find(c=>c.key===state.core), topic=model.topics.find(t=>t.id===state.topic);
  const root={id:'system:sfera',title:'CLIENT',kind:'root',color:'#bd443a',mark:'G',count:model.knowledge.length,x:380,y:340};
  let nodes=[root], links=[], detail=false, height=710;
  const subtopics=topicsForCore(model,core?.key);
  const level=preview?'overview':state.graphLevel||'overview';
  if(level==='overview'||!core) {
    // Fixed cognitive anchors. The center is intentionally reserved for the system.
    const slots=[[210,145],[550,145],[630,325],[550,505],[380,600],[210,505],[130,325],[380,70]];
    nodes.push(...model.cores.map((c,i)=>({...c,x:slots[i][0],y:slots[i][1]})));
    links=model.cores.map(c=>({id:`structure:${c.id}`,a:root.id,b:c.id,kind:'structure'}));
    links.push(...model.groups.map(g=>({...g,kind:'aggregate',count:g.edges.length})));
  } else if(level==='core' && subtopics.length&&!topic&&!state.showAll) {
    root.id=core.id; root.title=core.title; root.count=core.count; root.unit='материалов'; root.color=core.color; root.mark=core.mark;
    const slots=subtopics.length===4?[[210,145],[550,145],[550,505],[210,505]]:[[210,125],[550,125],[630,310],[550,505],[210,505],[130,310],[380,605],[380,65]];
    nodes.push(...subtopics.map((t,i)=>({...t,count:t.count,mark:String(i+1).padStart(2,'0'),x:slots[i][0],y:slots[i][1]})));
    links=nodes.slice(1).map(n=>({id:`structure:${n.id}`,a:root.id,b:n.id,kind:'structure'}));
    const topicByNode=new Map(subtopics.flatMap(t=>t.nodes.map(n=>[n.id,t.id]))), grouped=new Map();
    model.relations.forEach(e=>{
      const a=topicByNode.get(e.source_item_id),b=topicByNode.get(e.target_item_id);
      if(!a||!b||a===b)return;
      const pair=[a,b].sort(),key=pair.join('|');
      if(!grouped.has(key))grouped.set(key,{id:`topic-edge:${key}`,a:pair[0],b:pair[1],kind:'aggregate',count:0});
      grouped.get(key).count++;
    });
    links.push(...grouped.values());
  } else {
    detail=true;
    const active=model.byId.get(state.selected);
    let members;
    if(level==='node' && active) {
      Object.assign(root,active,{kind:'focus',color:core.color,x:380,y:340});
      const neighborhood=model.relations.filter(e=>e.source_item_id===active.id||e.target_item_id===active.id);
      const ids=[...new Set(neighborhood.map(e=>e.source_item_id===active.id?e.target_item_id:e.source_item_id))];
      members=ids.map(id=>model.byId.get(id));
    } else {
      Object.assign(root,{id:topic?.id||core.id,title:topic?.title||core.title,color:topic?.color||core.color,count:topic?topic.count:core.count,unit:'материалов',mark:core.mark});
      members=topic?topicMembers(topic):core.nodes;
      if(!topic) members=[...members,...core.documents.map(d=>({id:`document:${d.id}`,title:d.content.relative_path,kind:'document'})),...core.skills.map(s=>({id:`skill:${s.key}`,title:s.title,kind:'procedure'}))];
    }
    const page=state.corePage||0, shown=state.showAll?members:members.slice(page*8,page*8+8);
    const slots=[[170,95],[590,95],[170,240],[590,240],[170,425],[590,425],[170,570],[590,570]];
    if(state.showAll && shown.length>8) {
      root.x=380;root.y=105;height=260+Math.ceil(shown.length/2)*122;
      shown.forEach((n,i)=>slots[i]=[i%2?590:170,280+Math.floor(i/2)*122]);
    }
    nodes.push(...shown.map((n,i)=>({...n,color:CORE_DEFS.find(c=>c.key===n.area)?.color||core.color,x:slots[i][0],y:slots[i][1],card:true})));
    const displayed=new Set(nodes.map(n=>n.id));
    if(level!=='node') links=shown.map(n=>({id:`member:${n.id}`,a:root.id,b:n.id,kind:'structure'}));
    links.push(...model.relations.filter(e=>displayed.has(e.source_item_id)&&displayed.has(e.target_item_id)).map(e=>({id:e.id,a:e.source_item_id,b:e.target_item_id,kind:'semantic',proof:e.verification_status,label:e.relation_type})));
    root.pageInfo=!state.showAll&&members.length>8?`${page*8+1}–${Math.min(members.length,page*8+8)} / ${members.length}`:null;
  }
  return {nodes,links,detail,model,height};
}
function cognitiveSvg(preview) {
  const {nodes,links,detail,height}=cognitiveLayout(preview), positions=new Map(nodes.map(n=>[n.id,[n.x,n.y]]));
  if(!preview) {
    const key=nodes.map(n=>n.id).join('|');
    if(graphRuntime.key===key) {
      for(const [id,point] of graphRuntime.positions) if(positions.has(id)) positions.set(id,point);
    }
    if(graphRuntime.key!==key) { graphRuntime.key=key; graphRuntime.positions=new Map(positions); graphRuntime.panX=0;graphRuntime.panY=0;graphRuntime.zoom=1; }
  }
  const edges=links.map(e=>{
    const a=positions.get(e.a), b=positions.get(e.b), proposed=e.proof==='model_proposed';
    const aggregate=e.kind==='aggregate';
    const title=e.kind==='structure'?'Структура карты · принадлежность к ядру':aggregate?`${e.count} сохранённых отношений между ядрами`: `${e.label} · ${proposed?'предложение модели':e.proof==='source_backed'?'подтверждено источником':'редакторская связь'}`;
    const label=aggregate?`<title>${escapeHtml(title)}</title>`:`<title>${escapeHtml(title)}</title>`;
    const branch=height>710&&e.kind==='structure';
    return `<line data-edge-id="${escapeHtml(e.id)}" data-from="${escapeHtml(e.a)}" data-to="${escapeHtml(e.b)}" x1="${a[0]}" y1="${branch?b[1]:a[1]}" x2="${b[0]}" y2="${b[1]}" class="graph-edge cognitive-edge ${e.kind} ${branch?'tree-branch':''} ${proposed?'proposed':''}" ${aggregate?`style="stroke-width:${1+Math.min(3,Math.log2(e.count+1)*.5)}"`:''}>${label}</line>`;
  }).join('');
  const dots=nodes.map((n,index)=>{
    const [x,y]=positions.get(n.id), root=index===0;
    const titleLines=splitGraphLabel(n.title,n.card?27:root?(detail?20:12):22,n.card?3:root&&detail?3:2);
    let inner;
    if(n.card) {
      inner=`<rect class="cognitive-card" x="-146" y="-49" width="292" height="98" rx="12"/><rect x="-146" y="-33" width="3" height="34" rx="1" fill="${n.color}"/><text class="card-label" text-anchor="start" x="-126" y="-17">${titleLines.map((s,i)=>`<tspan x="-126" dy="${i?19:0}">${escapeHtml(s)}</tspan>`).join('')}</text><text class="card-meta" text-anchor="start" x="-126" y="36">${n.kind==='document'?'Канонический документ':n.kind==='procedure'?'Активная процедура':n.status==='agent_inference'?'○  Предложение модели':'●  '+(cognitiveModelStatus(n.status))}</text>`;
    } else if(root) {
      inner=`<circle class="core-aura" r="102"/><circle class="core-orbit" r="91"/><circle class="root-disc" r="${detail?79:72}"/><text class="root-kicker" y="${detail?-48:-29}">${detail?'ЯДРО / ФОКУС':'КОГНИТИВНОЕ ЯДРО'}</text><text class="root-title ${detail||titleLines.length>1?'root-title-detail':''}" y="${detail?-15:titleLines.length>1?-4:8}">${titleLines.map((s,i)=>`<tspan x="0" dy="${i?21:0}">${escapeHtml(s)}</tspan>`).join('')}</text><text class="root-meta" y="${detail?60:43}">${n.pageInfo|| (n.kind==='focus'?'СВЯЗИ ЗНАНИЯ':`${n.unit?'Материалов':'Знаний'}: ${n.count}`)}</text>`;
    } else {
      inner=`<circle class="satellite-aura" r="51"/><circle class="satellite-disc" r="36"/><text class="satellite-mark" y="8">${escapeHtml(n.mark||'З')}</text><circle class="count-disc" cx="32" cy="-27" r="17"/><text class="satellite-count" x="32" y="-22">${n.count}</text><text class="satellite-title" y="65">${titleLines.map((s,i)=>`<tspan x="0" dy="${i?20:0}">${escapeHtml(s)}</tspan>`).join('')}</text>${n.kind==='core'?`<text class="satellite-subtitle" y="86">${n.count?escapeHtml(n.subtitle):'Пока нет записей'}</text>`:''}`;
    }
    return `<g class="graph-node cognitive-node ${root?'is-hub cognitive-root':n.card?'is-card':'is-hub'} ${n.count===0?'is-empty':''}" data-node="${escapeHtml(n.id)}" data-node-area="${escapeHtml(n.area||n.key||'')}" data-x="${x}" data-y="${y}" data-fixed="${root?'true':'false'}" transform="translate(${x} ${y})" style="--core-color:${n.color};color:${n.color}" tabindex="0" role="button" aria-label="${escapeHtml(n.title)}${n.count!==undefined?` · ${n.count} материалов`:''}"><title>${escapeHtml(n.title)}</title>${inner}</g>`;
  }).join('');
  const trunk=height>710?`<line class="tree-trunk" x1="${nodes[0].x}" y1="${nodes[0].y}" x2="${nodes[0].x}" y2="${Math.max(...nodes.map(n=>n.y))}" stroke="#b6c2c5" stroke-width="1.5" stroke-dasharray="4 5"/>`:'<circle class="map-orbit" cx="380" cy="340" r="245"/>';
  return `<div class="graph-canvas cognitive-canvas ${preview?'preview':''} ${height>710?'expanded-canvas':''}" style="aspect-ratio:760/${height}" id="graph-canvas"><svg xmlns="http://www.w3.org/2000/svg" id="graph-svg" viewBox="0 0 760 ${height}" aria-label="Когнитивная карта CLIENT: центральное ядро, области и сохранённые отношения"><defs><pattern id="cognitive-grid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".8" fill="#d9e1e5"/></pattern><radialGradient id="cognitive-glow"><stop stop-color="#f4e8e2"/><stop offset="1" stop-color="#fafbf9"/></radialGradient></defs><rect width="760" height="${height}" fill="#fafbf9"/><rect width="760" height="${height}" fill="url(#cognitive-grid)"/><g id="graph-scene">${trunk}${edges}${dots}</g></svg></div>`;
}
function cognitiveModelStatus(status) { return {verified_fact:'Проверенный факт',accepted_decision:'Принятое решение',user_assertion:'Утверждение пользователя'}[status]||'Знание'; }
function splitGraphLabel(text,limit,maxLines) {
  const words=String(text).split(/\s+/), lines=[]; let current='';
  for(const word of words) {
    if(current && (current+' '+word).length>limit) {lines.push(current);current=word;} else current+=(current?' ':'')+word;
  }
  if(current) lines.push(current);
  const result=lines.slice(0,maxLines).map(s=>s.length>limit+4?s.slice(0,limit+1)+'…':s);
  if(lines.length>maxLines) result[maxLines-1]=result[maxLines-1].replace(/…$/,'')+'…';
  return result;
}
function bindCognitive() {
  app.querySelectorAll('[data-show-all]').forEach(el=>el.addEventListener('click',()=>{state.showAll=!state.showAll;state.corePage=0;graphRuntime.key=null;render();}));
  app.querySelectorAll('[data-cognitive-home]').forEach(el=>el.addEventListener('click',()=>{cognitiveHome();render();}));
  app.querySelectorAll('[data-core]').forEach(el=>el.addEventListener('click',()=>openCore(el.dataset.core)));
  app.querySelectorAll('[data-topic]').forEach(el=>el.addEventListener('click',()=>cognitiveSelect(el.dataset.topic)));
  app.querySelectorAll('[data-page]').forEach(el=>el.addEventListener('click',()=>{state.showAll=false;state.corePage=Math.max(0,(state.corePage||0)+Number(el.dataset.page));graphRuntime.key=null;render();}));
  app.querySelectorAll('[data-zoom]').forEach(el=>el.addEventListener('click',()=>{
    const next=Math.max(.55,Math.min(2.8,graphRuntime.zoom*(el.dataset.zoom==='in'?1.2:1/1.2)));
    graphRuntime.panX=380-(380-graphRuntime.panX)*next/graphRuntime.zoom;
    graphRuntime.panY=355-(355-graphRuntime.panY)*next/graphRuntime.zoom;
    graphRuntime.zoom=next;document.getElementById('graph-scene')?.setAttribute('transform',`translate(${graphRuntime.panX} ${graphRuntime.panY}) scale(${next})`);
  }));
}
