const API = '/galactica-api';
const PROJECT_ID = '@@PROJECT_ID@@';
const state = { view: ({'#access':'access','#graph':'graph','#dashboards':'dashboards','#confluence':'docs','#profile':'profile'})[location.hash]||'overview', area: 'all', graphMode: 'areas', graphQuery: '', pathStart: null, graphPath: null, query: '', items: [], documents: [], graph: null, health: null, selected: null, filter: 'all', loading: false, error: '', authenticated: null, access:null, accessDenied:'', deniedIdentity:null };
const graphRuntime = { key: null, positions: new Map(), raf: null, panX: 0, panY: 0, zoom: 1, ignoreClick: false };
const app = document.getElementById('app');

const icons = {
  overview: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  graph: '<circle cx="5" cy="12" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="m7 11 9-5M7 13l10 5M18 7v10"/>',
  docs: '<path d="M5 3h10l4 4v14H5z"/><path d="M15 3v5h4M8 12h8M8 16h8"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  logout: '<path d="M10 4H5v16h5M14 8l4 4-4 4m4-4H9"/>',
  close: '<path d="M5 5l14 14M19 5 5 19"/>',
  refresh: '<path d="M20 11a8 8 0 1 1-2-5M20 4v5h-5"/>',
  expand: '<path d="M8 3H3v5M16 3h5v5M3 16v5h5m13-5v5h-5"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  link: '<path d="M10 13a5 5 0 0 0 7 .2l2-2a5 5 0 0 0-7-7l-1.2 1.2M14 11a5 5 0 0 0-7-.2l-2 2a5 5 0 0 0 7 7l1.2-1.2"/>',
};
icons.access=icons.overview;icons.dashboards=icons.overview;icons.profile=icons.docs;
const icon = (name, size = 18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = value => value.length > 125 ? `${value.slice(0, 122)}…` : value;

async function request(path,options={}) {
  const response=await fetch(`${API}${path}`,{credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},...options});
  if(response.status===401){state.authenticated=false;state.accessDenied='';state.deniedIdentity=null;throw new Error('Для просмотра данных войди через Сфера.');}
  if(!response.ok){
    let body={};try{body=await response.json();}catch{}
    const detail=typeof body.detail==='string'?body.detail:`Сервис ответил кодом ${response.status}`;
    if(response.status===403&&body.reason_code==='GALACTICA_ACCESS_MISSING'){
      state.authenticated=false;state.accessDenied=detail;state.deniedIdentity=body.identity||null;
    }
    throw new Error(detail);
  }
  return response.json();
}

async function load() {
  state.loading = true; state.error = ''; render();
  try {
    const [items, documents, health, graph, access] = await Promise.all([
      request('/v1/daughter/knowledge'),
      request('/v1/daughter/documents'),
      request('/v1/daughter/health'),
      request('/v1/daughter/graph'),
      request('/v1/daughter/access'),
    ]);
    state.access=access;
    state.items = items;
    state.documents = documents;
    state.health = health;
    state.graph = graph;
    state.authenticated = true;state.accessDenied='';state.deniedIdentity=null;
    if(state.view==='access'&&access.can_admin) aclState.data=await request('/v1/daughter/admin/access');
    if(state.view==='dashboards'&&access.identity.acl_admin)loadMetrics();
  } catch (error) { state.error = error.message; }
  state.loading = false; render();
}

async function loadGraph(seedId, rerender = true) {
  try {
    state.graph = await request('/v1/daughter/graph');
    state.selected = seedId; state.showAll = false; state.graphLevel = 'node'; state.corePage = 0;
    state.core = state.graph.nodes.find(n => n.id === seedId)?.area || 'knowledge';
  } catch (error) { state.graph = {nodes: [], edges: [], truncated: false}; state.error = `Граф: ${error.message}`; }
  if (rerender) render();
}

function loginView() {
  app.innerHTML = `<main class="login-page"><div class="login-art"><div class="login-grid"></div><div class="orb orb-one"></div><div class="orb orb-two"></div><div class="login-art-content"><div class="brand-mark">A<span>·</span></div><p class="eyebrow">CLIENT / KNOWLEDGE SYSTEM</p><h1>Знания<br>в связях.</h1><p>Единое рабочее пространство Сфера CLIENT: факты, источники и контекст решений.</p><div class="login-art-foot">01 / Внутренний контур CLIENT</div></div></div><div class="login-form-wrap"><div class="login-form"><div class="mobile-brand">Сфера <span>· CLIENT</span></div><p class="eyebrow">ДОБРО ПОЖАЛОВАТЬ</p><h2>Вход в Сфера</h2><p class="muted">Используй собственную учётную запись этого экземпляра Сферы.</p><a class="primary-button" href="/login?next=/galactica/">Войти в Сфера ${icon('arrow',17)}</a><p class="login-note">После входа вы вернётесь в Сфера.</p></div></div></main>`;
}
const AREAS = [
  {key:'work', title:'Работа CLIENT', subtitle:'Требования, решения и результаты работы'},
  {key:'skills', title:'Навыки', subtitle:'Активные навыки и кандидаты'},
  {key:'knowledge', title:'Знания', subtitle:'Общие методики и правила'},
  {key:'experts', title:'Эксперты', subtitle:'Профили экспертов этого клиента'},
  {key:'threads', title:'Треды Codex', subtitle:'Выводы из локальных задач CLIENT'},
  {key:'self', title:'Я', subtitle:'Правила и устройство агента'},
  {key:'world', title:'Мир', subtitle:'Окружение, базы и подключения · ссылки на исходные записи'},
  {key:'user', title:'Пользователь', subtitle:'Явные предпочтения'},
];
function areaOf(item) {
  return AREAS.some(area => area.key === item.content?.area) ? item.content.area : 'knowledge';
}
function areaCounts() {
  const counts = Object.fromEntries(AREAS.map(a => [a.key,{knowledge:0,documents:0}]));
  state.items.forEach(item => {counts[areaOf(item)].knowledge++;if(areaOf(item)!=='world'&&worldTopic(item))counts.world.knowledge++;});
  state.documents.forEach(item => {counts[areaOf(item)].documents++;if(areaOf(item)!=='world'&&worldTopic(item))counts.world.documents++;});
  return counts;
}
function shell(content) {return workspaceShell(content);}
function nav(view, label) { const active = state.view === view && (view !== 'docs' || state.area === 'all'); return `<button class="nav-button ${active ? 'active' : ''}" data-view="${view}" ${active ? 'aria-current="page"' : ''}>${icon(view)}<span>${label}</span>${active ? '<i></i>' : ''}</button>`; }
function navArea(area) { const active = state.view === 'docs' && state.area === area.key; return `<button class="nav-button area-nav ${active ? 'active' : ''}" data-area="${area.key}" ${active ? 'aria-current="page"' : ''}>${icon('docs')}<span>${area.title}</span>${active ? '<i></i>' : ''}</button>`; }
function heading(kicker, title, text, right = '') { return `<div class="page-heading"><div><p class="eyebrow">${kicker}</p><h1>${title}</h1><p class="page-desc">${text}</p></div>${right}</div>`; }
function badge(kind, status) { const names = {fact:'Факт',failure:'Ошибка',requirement:'Требование',decision:'Решение',constraint:'Ограничение',observation:'Наблюдение',goal:'Цель',document:'Документ',expert_profile:'Эксперт · planned',skill_candidate:'Кандидат навыка',engineering_pattern:'Методика',task_insight:'Вывод из задачи',operational_method:'Рабочая методика',source_limit:'Ограничение источника',data_catalog_method:'Каталог данных',metric_caveat:'Оговорка к метрике'}; const label = kind === 'failure' && status === 'agent_inference' ? 'Вывод модели · тип не проверен' : names[kind] || kind || 'Знание'; return `<span class="kind-badge kind-${escapeHtml(kind)}">${escapeHtml(label)}</span>`; }
function kindCounts() { return state.items.reduce((acc, item) => { const kind = item.content?.kind || 'knowledge'; acc[kind] = (acc[kind] || 0) + 1; return acc; }, {}); }
function overviewView() {
  const total = state.items.length, nodes = state.health?.node_count, edges = state.health?.edge_count;
  const selected = [...state.documents, ...state.items].slice(0, 5);
  const areas = areaCounts();
  return shell(`${heading('КОМАНДНЫЙ ЦЕНТР / 01', 'Обзор Сферы', 'Области знаний Сферы CLIENT. Извлечённые тезисы, документы, навыки и архив задач учитываются отдельно.', `<button class="outline-button" data-area="work">Работа CLIENT ${icon('arrow',16)}</button>`)}
    <div class="section-caption"><span>КАНОНИЧЕСКИЙ КОНТУР</span><span>Данные из защищённого API</span></div>
    <div class="metrics"><div class="metric-card accent"><div class="metric-top">ИЗВЛЕЧЁННЫЕ ЗНАНИЯ ${icon('docs',20)}</div><strong>${total}</strong><span>активных тезисов</span><div class="metric-line"></div></div><div class="metric-card"><div class="metric-top">ДОКУМЕНТЫ ${icon('docs',20)}</div><strong>${state.health?.document_count ?? '—'}</strong><span>канонических страниц</span></div><div class="metric-card"><div class="metric-top">НАВЫКИ ${icon('overview',20)}</div><strong>${state.health?.skill_count ?? '—'}</strong><span>активных процедур</span></div><div class="metric-card"><div class="metric-top">ЗАДАЧИ ${icon('link',20)}</div><strong>${state.health?.task_count ?? '—'}</strong><span>записей в архиве</span></div></div>
    <div class="section-caption lower"><span>ОБЛАСТИ ЗНАНИЙ</span><span>Классификация из PostgreSQL</span></div>
    <div class="area-grid">${AREAS.map(area => { const count = areas[area.key]; const skills = area.key === 'skills' ? state.health?.skill_count || 0 : 0; return `<button class="area-card" data-area="${area.key}"><span class="area-card-title">${area.title} ${icon('arrow',15)}</span><span class="area-card-desc">${area.subtitle}</span><strong>${count.knowledge + count.documents + skills}</strong><small>${count.knowledge ? `${count.knowledge} знаний · ` : ''}${count.documents ? `${count.documents} страниц${skills ? ' · ' : ''}` : ''}${skills ? `${skills} навык` : ''}${!count.knowledge && !count.documents && !skills ? 'Пока нет записей' : ''}</small></button>`; }).join('')}</div>
    <div class="overview-grid"><section class="panel graph-preview"><div class="panel-heading"><div><p class="eyebrow">ПРОЕКТ / CLIENT</p><h2>Когнитивная карта CLIENT</h2></div><button class="text-button" data-view="graph">Исследовать ${icon('arrow',15)}</button></div>${graphSvg(true)}<div class="graph-preview-foot">8 ядер · ${nodes ?? '—'} знаний · ${edges ?? '—'} сохранённых отношений</div></section><section class="panel recent-panel"><div class="panel-heading"><div><p class="eyebrow">КАНОНИЧЕСКАЯ БАЗА</p><h2>Материалы</h2></div><button class="text-button" data-view="docs">Все материалы ${icon('arrow',15)}</button></div><div class="recent-list">${selected.length ? selected.map(item => `<button class="recent-item" data-item="${item.id}"><span class="recent-icon">${icon('docs',17)}</span><span><strong>${escapeHtml(short(item.title))}</strong><small>${escapeHtml(item.content?.kind || 'Знание')}</small></span>${icon('chevron',15)}</button>`).join('') : '<div class="empty-inline">Пока нет доступных материалов.</div>'}</div></section></div>`);
}

function docsView() {
  const area = AREAS.find(a => a.key === state.area);
  const items = [...state.documents, ...state.items].filter(item => (state.area === 'all' || areaOf(item) === state.area || state.area === 'world' && worldTopic(item)) && (['all','knowledge-search'].includes(state.filter) || (item.content?.kind || 'knowledge') === state.filter) && `${item.title} ${JSON.stringify(item.content)}`.toLowerCase().includes(state.query.toLowerCase()));
  const kinds = ['document', ...Object.keys(kindCounts())];
  const skills = state.area === 'skills' || state.area === 'all' ? state.health?.skills || [] : [];
  return shell(`${heading('БАЗА ЗНАНИЙ / 03', area?.title || 'Все материалы', area?.subtitle || 'Страницы, знания и навыки автономного контура CLIENT.')}<div class="docs-layout"><section class="panel docs-panel"><div class="docs-toolbar"><label class="search-box">${icon('search',18)}<input id="search-input" type="search" placeholder="Поиск по материалам..." value="${escapeHtml(state.query)}"><kbd>⌘ K</kbd></label><select id="kind-filter" aria-label="Фильтр по типу"><option value="all">Все типы</option>${kinds.map(kind => `<option value="${escapeHtml(kind)}" ${state.filter === kind ? 'selected' : ''}>${escapeHtml(kind)}</option>`).join('')}</select></div><div class="list-count">${items.length} материалов${skills.length ? ` · ${skills.length} активных навыков` : ''}</div><div class="docs-list">${skills.map(skill => `<div class="doc-row skill-row"><span class="doc-icon">${icon('overview',19)}</span><span class="doc-row-body"><strong>${escapeHtml(skill.title)}</strong><small><span class="kind-badge">Навык · v${escapeHtml(skill.version)}</span> ${escapeHtml(skill.scope)}</small></span></div>`).join('')}${items.length ? items.map(item => `<button class="doc-row ${state.selected === item.id ? 'selected' : ''}" data-item="${item.id}"><span class="doc-icon">${icon('docs',19)}</span><span class="doc-row-body"><strong>${escapeHtml(item.title)}</strong><small>${badge(item.content?.kind || 'knowledge', item.content?.epistemic_status)} <span>·</span> ${escapeHtml(short(String(item.content?.summary || item.content?.text || item.content?.body || item.content?.relative_path || 'Открыть карточку знания')))}</small></span>${icon('chevron',16)}</button>`).join('') : !skills.length ? '<div class="empty-inline">В этой области пока нет проверенных записей CLIENT.</div>' : ''}</div></section><aside class="docs-aside">${AREAS.map(navArea).join('')}<button class="outline-button" data-view="docs">К дереву страниц</button><div class="aside-label">ОБЛАСТЬ</div><h3>${escapeHtml(area?.title || 'Все материалы')}</h3><p>${escapeHtml(area?.subtitle || 'Канонические страницы и извлечённые знания показаны отдельно от архива задач.')}</p><div class="aside-rule"></div><span>Контур</span><strong>CLIENT</strong><span>Источник</span><strong>Сфера CLIENT</strong></aside></div>`);
}

function graphView() { return fullGraphView(); }

function graphSearchResults(graph) {
  const query = state.graphQuery.trim().toLocaleLowerCase('ru');
  if (!query) return '';
  const matches = state.items.filter(node => node.title.toLocaleLowerCase('ru').includes(query)).slice(0, 8);
  return matches.length ? matches.map(node => `<button data-search-node="${escapeHtml(node.id)}">${escapeHtml(node.title)}</button>`).join('') : '<span>Совпадений нет</span>';
}

function shortestSemanticPath(graph, startId, targetId) {
  if (startId === targetId) return {nodeIds: [startId], edgeIds: []};
  const adjacency = new Map();
  (graph.edges || []).filter(edge => edge.edge_kind === 'semantic').forEach(edge => {
    for (const [from, to] of [[edge.source_item_id, edge.target_item_id], [edge.target_item_id, edge.source_item_id]]) {
      if (!adjacency.has(from)) adjacency.set(from, []);
      adjacency.get(from).push({to, edgeId: edge.id});
    }
  });
  const queue = [startId], previous = new Map([[startId, null]]);
  for (let index = 0; index < queue.length; index++) {
    const current = queue[index];
    for (const next of adjacency.get(current) || []) {
      if (previous.has(next.to)) continue;
      previous.set(next.to, {from: current, edgeId: next.edgeId});
      if (next.to === targetId) {
        const nodeIds = [targetId], edgeIds = [];
        let cursor = targetId;
        while (cursor !== startId) {
          const step = previous.get(cursor);
          edgeIds.unshift(step.edgeId);
          cursor = step.from;
          nodeIds.unshift(cursor);
        }
        return {nodeIds, edgeIds};
      }
      queue.push(next.to);
    }
  }
  return null;
}

async function selectGraphNode(nodeId, center = false) {
  if(state.view==='graph'){workspace.node=nodeId;render();return;}
  if (cognitiveSelect(nodeId)) return;
  let node = state.graph?.nodes.find(item => item.id === nodeId);
  if (!node && state.items.some(item => item.id === nodeId)) {
    await loadGraph(nodeId, false);
    node = state.graph?.nodes.find(item => item.id === nodeId);
  }
  if (!node) return;
  if (state.pathStart && state.pathStart !== nodeId) {
    if (node.kind === 'area' || node.kind === 'source') {
      state.error = 'Для пути выберите знание, а не область или источник.';
    } else {
      state.graphPath = shortestSemanticPath(state.graph, state.pathStart, nodeId);
      if (!state.graphPath) state.error = 'Между этими знаниями нет сохранённого смыслового пути.';
      state.pathStart = null;
    }
  }
  state.selected = nodeId; state.showAll = false; state.graphLevel = 'node';
  const currentCore = cognitiveModel().cores.find(c => c.key === state.core);
  if (!currentCore?.nodes.some(n => n.id === nodeId)) { state.core = node.area; state.topic = null; }
  state.corePage = 0; graphRuntime.key = null;
  if (center) { state.graphQuery = ''; }
  if(state.view==='docs')state.view='overview';
  render();
}

function graphNodeDetail(node) {
  if (!node) return '<div class="graph-hint"><h3>Выберите узел</h3><p>Нажмите на область или знание, чтобы увидеть его содержание и связи.</p></div>';
  const edges = (state.graph?.edges || []).filter(e => e.source_item_id === node.id || e.target_item_id === node.id);
  const semantic = edges.filter(e => e.edge_kind === 'semantic');
  if (node.kind === 'area') {
    const linked = edges.filter(e => e.edge_kind === 'membership').map(e => (state.graph?.nodes || []).find(n => n.id === e.target_item_id)).filter(Boolean);
    return `<div class="detail-content"><span class="kind-badge">ОБЛАСТЬ ЗНАНИЙ</span><h2>${escapeHtml(node.title)}</h2><p class="detail-text">${escapeHtml(node.description || '')}</p><p class="muted">${node.member_count} знаний в базе, ${linked.length} показано на карте. Принадлежность к области не означает смысловую связь.</p><button class="outline-button detail-action" data-area="${escapeHtml(node.area)}">Все материалы области ${icon('arrow',14)}</button><div class="graph-neighbors"><strong>На карте · ${linked.length}</strong>${linked.map(item => `<button class="neighbor-button" data-node="${escapeHtml(item.id)}">${escapeHtml(item.title)} ${icon('arrow',13)}</button>`).join('')}</div></div>`;
  }
  if (node.kind === 'source') {
    const linked = edges.filter(e => e.edge_kind === 'provenance').map(e => (state.graph?.nodes || []).find(n => n.id === (e.source_item_id === node.id ? e.target_item_id : e.source_item_id))).filter(Boolean);
    const date = node.source_date ? new Date(node.source_date).toLocaleDateString('ru-RU') : 'Не указана';
    const dateLabel = node.source_type === 'Локальный тред Codex' ? 'Последний итог' : 'Дата источника';
    return `<div class="detail-content"><span class="kind-badge">${escapeHtml(node.source_type || 'Источник')}</span><h2>${escapeHtml(node.title)}</h2><p class="detail-text">Пунктир показывает происхождение знаний и не означает смысловую связь между ними.</p><div class="detail-meta"><span>${dateLabel}</span><strong>${escapeHtml(date)}</strong><span>Идентификатор</span><code>${escapeHtml(node.source_ref || 'Не указан')}</code></div><div class="detail-rule"></div><div class="graph-neighbors"><strong>Связанные знания · ${linked.length}</strong>${linked.map(item => `<button class="neighbor-button" data-node="${escapeHtml(item.id)}">${escapeHtml(item.title)} ${icon('arrow',13)}</button>`).join('')}</div></div>`;
  }
  const item = state.items.find(x => x.id === node.id);
  return `${detailContent(item, false)}<div class="detail-rule"></div><div class="graph-neighbors"><strong>Смысловые связи · ${semantic.length}</strong>${semantic.length ? semantic.map(edge => { const other = (state.graph?.nodes || []).find(n => n.id === (edge.source_item_id === node.id ? edge.target_item_id : edge.source_item_id)); const proof = edge.verification_status === 'source_backed' ? 'точный источник' : edge.verification_status === 'model_proposed' ? 'предложение модели' : 'редакторская'; return other ? `<button class="neighbor-button" data-node="${escapeHtml(other.id)}"><span>${escapeHtml(edge.relation_type)} · ${proof}</span>${escapeHtml(other.title)} ${icon('arrow',13)}</button>` : ''; }).join('') : '<p>Для этого знания смысловая связь ещё не записана.</p>'}</div>`;
}

function detailContent(item, compact = false) {
  if (!item) return '<p class="muted">Материал недоступен.</p>';
  const content = item.content || {};
  const body = String(content.body || content.text || content.summary || content.description || 'Описание пока не добавлено.');
  const origin = Array.isArray(content.source_spans) ? content.source_spans[0] : null;
  const sourceLabel = origin?.source_system === 'galactica_parent' ? 'Материнская GALACTICA' : origin?.source_system === 'trend_runtime' ? 'Работающий Сфера' : origin?.source_system === 'codex_local' ? `Локальный тред Codex · ${origin.source_title || ''}` : origin?.source_system === 'task_outcome' ? 'Аутком задачи CLIENT' : 'Каноническая база CLIENT';
  const sourceId = origin?.source_canonical_id ? `<span>Исходная запись</span><code>${escapeHtml(origin.source_canonical_id)}</code>` : '';
  const area = AREAS.find(entry => entry.key === content.area)?.title || 'Без области';
  return `<div class="detail-content">${badge(content.kind || 'knowledge', content.epistemic_status)} ${content.epistemic_status === 'agent_inference' && content.kind !== 'failure' ? '<span class="kind-badge">Предложение модели</span>' : ''}<h2>${escapeHtml(item.title)}</h2><p class="detail-text">${escapeHtml(body)}</p><div class="detail-rule"></div><div class="detail-meta"><span>Область</span><strong>${escapeHtml(area)}</strong><span>Идентификатор</span><code>${escapeHtml(item.id)}</code><span>Источник</span><strong>${escapeHtml(sourceLabel)}</strong>${sourceId}</div>${content.kind === 'document' ? '' : compact ? `<button class="primary-button detail-action" data-action="expand" data-seed="${item.id}">Раскрыть связи ${icon('arrow',16)}</button>` : `<button class="outline-button detail-action" data-action="show-in-graph" data-seed="${item.id}">Показать связи ${icon('graph',16)}</button>`}</div>`;
}

function graphSvg(preview) { return cognitiveSvg(preview); }

function render() {
  if (state.authenticated === null) { app.innerHTML = '<div class="loading-screen">Сфера <span>·</span> CLIENT</div>'; return; }
  if (!state.authenticated) { if(state.accessDenied) accessDeniedView(); else loginView(); return; }
  const content = state.view === 'access' ? accessAdminView() : state.view === 'graph' ? graphView() : state.view === 'docs' ? confluenceView() : state.view === 'dashboards' ? dashboardsView() : state.view === 'profile' ? profileView() : galaxyHome();
  app.innerHTML = `${content}${state.error ? `<div class="toast" role="alert">${escapeHtml(state.error)}<button data-action="dismiss" aria-label="Закрыть">${icon('close',14)}</button></div>` : ''}${state.loading ? '<div class="loading-bar"></div>' : ''}`;
  bind();
}

function bind() {
  bindAccessAdmin();
  app.querySelectorAll('[data-view]').forEach(el => el.addEventListener('click', () => { state.view = el.dataset.view; history.replaceState(null,'',({access:'#access',graph:'#graph',dashboards:'#dashboards',docs:'#confluence',profile:'#profile'})[state.view]||location.pathname); if(state.view==='access'){loadAccessAdmin();return;} state.area='all';state.query='';state.filter='all';cognitiveHome();render();if(state.view==='dashboards')loadMetrics(); }));
  app.querySelectorAll('button[data-area]').forEach(el => el.addEventListener('click', () => { state.view = 'docs';history.replaceState(null,'','#confluence');state.area = el.dataset.area; state.filter = 'all'; state.query = ''; state.selected = null; render(); }));
  app.querySelectorAll('[data-item]').forEach(el => el.addEventListener('click', () => { state.selected = el.dataset.item; if (state.view === 'overview') state.view = 'docs'; render(); showDetail(); }));
  app.querySelectorAll('[data-node]').forEach(el => {
    const select = () => { if (graphRuntime.ignoreClick) return; selectGraphNode(el.dataset.node); };
    el.addEventListener('click', select); el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectGraphNode(el.dataset.node); } });
  });
  app.querySelectorAll('[data-search-node]').forEach(el => el.addEventListener('click', () => selectGraphNode(el.dataset.searchNode, true)));
  app.querySelectorAll('[data-action]').forEach(el => el.addEventListener('click', async () => {
    if (el.dataset.action === 'logout') { try { await fetch('/logout', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'}); } catch {} state.authenticated = false; state.items=[]; state.documents=[]; state.graph=null; window.location.assign('/login?next=/galactica/'); }
    if (el.dataset.action === 'refresh') load();
    if (el.dataset.action === 'mode-areas') { state.graphMode = 'areas'; state.selected = null; state.graphPath = null; state.pathStart = null; render(); }
    if (el.dataset.action === 'mode-sources') { state.graphMode = 'sources'; state.selected = null; state.graphPath = null; state.pathStart = null; render(); }
    if (el.dataset.action === 'dismiss') { state.error=''; render(); }
    if (el.dataset.action === 'show-in-graph') { state.view='graph'; await loadGraph(el.dataset.seed); render(); }
    if (el.dataset.action === 'expand') { await loadGraph(el.dataset.seed); render(); }
    if (el.dataset.action === 'focus-selected' && el.dataset.seed) { state.selected = el.dataset.seed; state.graphPath = null; state.pathStart = null; await loadGraph(state.selected, false); render(); }
    if (el.dataset.action === 'all-graph') { try { state.graph = await request('/v1/daughter/graph'); state.selected = null; state.graphPath = null; state.pathStart = null; render(); } catch (error) { state.error = `Граф: ${error.message}`; render(); } }
    if (el.dataset.action === 'path-start' && el.dataset.seed) { state.selected = el.dataset.seed; state.pathStart = state.selected; state.graphPath = null; render(); }
    if (el.dataset.action === 'reset-path') { state.pathStart = null; state.graphPath = null; render(); }
    if (el.dataset.action === 'fit') { graphRuntime.key = null; graphRuntime.panX = 0; graphRuntime.panY = 0; graphRuntime.zoom = 1; render(); }
  }));
  const search = document.getElementById('search-input'); if(search) search.addEventListener('input', e => { const pos=e.target.selectionStart; state.query=e.target.value; render(); const next=document.getElementById('search-input'); next.focus(); next.setSelectionRange(pos,pos); });
  const filter = document.getElementById('kind-filter'); if(filter) filter.addEventListener('change', e => { state.filter=e.target.value; render(); });
  const graphSearch = document.getElementById('graph-search-input'); if (graphSearch) graphSearch.addEventListener('input', e => {
    state.graphQuery = e.target.value;
    const results = document.getElementById('graph-search-results');
    results.innerHTML = graphSearchResults(state.graph || {nodes:[]});
    results.querySelectorAll('[data-search-node]').forEach(button => button.addEventListener('click', () => selectGraphNode(button.dataset.searchNode, true)));
  });
  document.onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault();state.view='docs';state.area='all';state.filter='knowledge-search';history.replaceState(null,'','#confluence');render();document.getElementById('search-input')?.focus(); } if(e.key === 'Escape') document.querySelector('.detail-drawer')?.remove(); };
  bindWorkspace();
  bindCognitive();
  setupGraphInteraction();
}

function showDetail() {
  const item=[...state.documents, ...state.items].find(x=>x.id===state.selected); if(!item) return;
  const drawer=document.createElement('div'); drawer.className='detail-drawer'; drawer.innerHTML=`<div class="drawer-shade" data-close></div><section class="drawer-panel"><div class="drawer-header"><span>МАТЕРИАЛ / CLIENT</span><button class="icon-button" data-close aria-label="Закрыть">${icon('close')}</button></div>${detailContent(item)}</section>`;
  app.append(drawer); drawer.querySelectorAll('[data-close]').forEach(el=>el.addEventListener('click',()=>drawer.remove()));
  drawer.querySelector('[data-action="show-in-graph"]')?.addEventListener('click',async()=>{drawer.remove();state.view='graph';workspace.node=item.id;render();});
}

function setupGraphInteraction() {
  if (graphRuntime.raf) cancelAnimationFrame(graphRuntime.raf);
  graphRuntime.raf = null;
  const canvas = document.querySelector('.graph-workspace #graph-canvas');
  if (!canvas) return;
  const svg = canvas.querySelector('svg');
  const scene = canvas.querySelector('#graph-scene');
  const box = svg.viewBox.baseVal;
  const particles = [...scene.querySelectorAll('.graph-node')].map(element => ({
    id: element.dataset.node, element, x: Number(element.dataset.x), y: Number(element.dataset.y),
    ax: Number(element.dataset.x), ay: Number(element.dataset.y),
    area: element.dataset.nodeArea, fixed: element.dataset.fixed === 'true', vx: 0, vy: 0, hub: element.classList.contains('is-hub'), dragging: false,
  }));
  const byId = new Map(particles.map(particle => [particle.id, particle]));
  const links = [...scene.querySelectorAll('.graph-edge')].map(element => ({
    element, from: byId.get(element.dataset.from), to: byId.get(element.dataset.to),
    kind: element.classList.contains('semantic') ? 'semantic' : 'structure',
  })).filter(link => link.from && link.to);
  let frames = 0, drag = null, pan = null;
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const expandedLayout = canvas.classList?.contains('expanded-canvas');

  function transformScene() {
    scene.setAttribute('transform', `translate(${graphRuntime.panX} ${graphRuntime.panY}) scale(${graphRuntime.zoom})`);
  }
  function svgPoint(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  }
  function scenePoint(event) {
    const point = svgPoint(event);
    return {x: (point.x - graphRuntime.panX) / graphRuntime.zoom,
            y: (point.y - graphRuntime.panY) / graphRuntime.zoom};
  }
  function paint() {
    for (const particle of particles) {
      particle.element.setAttribute('transform', `translate(${particle.x.toFixed(2)} ${particle.y.toFixed(2)})`);
      graphRuntime.positions.set(particle.id, [particle.x, particle.y]);
    }
    for (const link of links) {
      link.element.setAttribute('x1', link.from.x.toFixed(2));
      link.element.setAttribute('y1', (link.element.classList.contains('tree-branch') ? link.to.y : link.from.y).toFixed(2));
      link.element.setAttribute('x2', link.to.x.toFixed(2));
      link.element.setAttribute('y2', link.to.y.toFixed(2));
    }
    const trunk = scene.querySelector?.('.tree-trunk');
    if (trunk && particles.length) {
      trunk.setAttribute('x1', particles[0].x); trunk.setAttribute('x2', particles[0].x);
      trunk.setAttribute('y1', particles[0].y); trunk.setAttribute('y2', Math.max(...particles.map(p => p.y)));
    }
  }
  function tick() {
    graphRuntime.raf = null;
    frames++;
    const forces = new Map(particles.map(particle => [particle.id, {
      x: (particle.ax - particle.x) * (particle.hub ? .13 : .085),
      y: (particle.ay - particle.y) * (particle.hub ? .13 : .085),
    }]));
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const a = particles[i], b = particles[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        const distance2 = dx * dx + dy * dy;
        if (distance2 > 16000) continue;
        if (distance2 < 1) { dx = (i % 3 - 1) * 2 + 1; dy = (j % 3 - 1) * 2 + 1; }
        const distance = Math.max(1, Math.hypot(dx, dy));
        const strength = 360 / Math.max(160, distance2);
        const fx = dx / distance * strength, fy = dy / distance * strength;
        forces.get(a.id).x -= fx; forces.get(a.id).y -= fy;
        forces.get(b.id).x += fx; forces.get(b.id).y += fy;
      }
    }
    for (const link of links) {
      if (expandedLayout || link.kind !== 'semantic' || link.from.area !== link.to.area) continue;
      const dx = link.to.x - link.from.x, dy = link.to.y - link.from.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const desired = 90;
      const strength = (distance - desired) * .0007;
      const fx = dx / distance * strength, fy = dy / distance * strength;
      forces.get(link.from.id).x += fx; forces.get(link.from.id).y += fy;
      forces.get(link.to.id).x -= fx; forces.get(link.to.id).y -= fy;
    }
    let motion = 0;
    for (const particle of particles) {
      if (particle.dragging || particle.fixed) continue;
      const force = forces.get(particle.id);
      particle.vx = Math.max(-5, Math.min(5, (particle.vx + force.x) * .82));
      particle.vy = Math.max(-5, Math.min(5, (particle.vy + force.y) * .82));
      particle.x = Math.max(22, Math.min(box.width - 22, particle.x + particle.vx));
      particle.y = Math.max(22, Math.min(box.height - 22, particle.y + particle.vy));
      motion += particle.vx * particle.vx + particle.vy * particle.vy;
    }
    paint();
    if (frames < 180 && (motion > .03 || frames < 30 || drag)) graphRuntime.raf = requestAnimationFrame(tick);
  }
  function wake() {
    frames = 0;
    if (!graphRuntime.raf && !reducedMotion) graphRuntime.raf = requestAnimationFrame(tick);
  }
  function highlight(nodeId) {
    const related = new Set([nodeId]);
    for (const link of links) if (link.from.id === nodeId || link.to.id === nodeId) {
      related.add(link.from.id); related.add(link.to.id);
      link.element.classList.add('is-neighbor');
    }
    for (const particle of particles) particle.element.classList.toggle('is-dimmed', !related.has(particle.id));
    for (const link of links) link.element.classList.toggle('is-dimmed', !link.element.classList.contains('is-neighbor'));
  }
  function clearHighlight() {
    for (const particle of particles) particle.element.classList.remove('is-dimmed');
    for (const link of links) link.element.classList.remove('is-dimmed', 'is-neighbor');
  }

  transformScene();
  if (graphRuntime.focusId && byId.has(graphRuntime.focusId)) {
    const focus = byId.get(graphRuntime.focusId);
    graphRuntime.zoom = Math.max(graphRuntime.zoom, 1.3);
    graphRuntime.panX = box.width / 2 - focus.x * graphRuntime.zoom;
    graphRuntime.panY = box.height / 2 - focus.y * graphRuntime.zoom;
    graphRuntime.focusId = null;
    transformScene();
  }
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    const pointer = svgPoint(event);
    const before = scenePoint(event);
    graphRuntime.zoom = Math.max(.55, Math.min(2.8, graphRuntime.zoom * (event.deltaY < 0 ? 1.12 : .89)));
    graphRuntime.panX = pointer.x - before.x * graphRuntime.zoom;
    graphRuntime.panY = pointer.y - before.y * graphRuntime.zoom;
    transformScene();
  }, {passive: false});
  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const nodeElement = event.target.closest('.graph-node');
    if (nodeElement) {
      const particle = byId.get(nodeElement.dataset.node);
      if (!particle) return;
      const pointer = scenePoint(event);
      drag = {particle, dx: particle.x - pointer.x, dy: particle.y - pointer.y,
              startX: event.clientX, startY: event.clientY, moved: false};
      particle.dragging = true;
      wake();
    } else {
      const pointer = svgPoint(event);
      pan = {dx: graphRuntime.panX - pointer.x, dy: graphRuntime.panY - pointer.y};
    }
    (nodeElement || canvas).setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (drag) {
      const pointer = scenePoint(event);
      drag.particle.x = Math.max(22, Math.min(box.width - 22, pointer.x + drag.dx));
      drag.particle.y = Math.max(22, Math.min(box.height - 22, pointer.y + drag.dy));
      drag.moved ||= Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4;
      paint(); wake();
    } else if (pan) {
      const pointer = svgPoint(event);
      graphRuntime.panX = pointer.x + pan.dx;
      graphRuntime.panY = pointer.y + pan.dy;
      transformScene();
    }
  });
  const endPointer = () => {
    if (drag) {
      drag.particle.dragging = false;
      drag.particle.ax = drag.particle.x; drag.particle.ay = drag.particle.y;
      if (drag.moved) {
        graphRuntime.ignoreClick = true;
        setTimeout(() => { graphRuntime.ignoreClick = false; }, 0);
      }
      drag = null; wake();
    }
    pan = null;
  };
  canvas.addEventListener('pointerup', endPointer);
  canvas.addEventListener('pointercancel', endPointer);
  canvas.addEventListener('pointerover', event => {
    const node = event.target.closest('.graph-node');
    if (node && !drag) { clearHighlight(); highlight(node.dataset.node); }
  });
  canvas.addEventListener('pointerout', event => {
    if (event.target.closest('.graph-node') && !event.relatedTarget?.closest?.('.graph-node')) clearHighlight();
  });
  paint();
  wake();
}

load();

function accessDeniedView(){
  loginView();
  const form=app.querySelector('.login-form');
  form.innerHTML=`<p class="eyebrow">ВХОД Сфера ВЫПОЛНЕН</p><h2>Доступ к Сфера не выдан</h2><p class="muted">${escapeHtml(state.deniedIdentity?.username?`Ты вошёл как ${state.deniedIdentity.username}.`:'')}</p><p class="muted">${escapeHtml(state.accessDenied)}</p><p class="login-note">Администратор этого экземпляра должен проверить пользователя и его права. Для админки также нужно «Администратор Сферы».</p><button class="primary-button" id="galactica-change-account" type="button">Войти под другой учётной записью</button><button class="outline-button" id="galactica-recheck-access" type="button">Проверить доступ снова</button>`;
  form.dataset.sourceSubject=state.deniedIdentity?.source_subject_key||'';
  form.querySelector('#galactica-change-account').addEventListener('click',async()=>{
    await fetch('/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});
    location.assign('/login?next=/galactica/');
  });
  form.querySelector('#galactica-recheck-access').addEventListener('click',()=>load());
}

// Recheck permission revision while the page is open; do not keep revoked cards.
setInterval(async()=>{
  if(!state.authenticated||state.loading||state.view==='access')return;
  try{const access=await request('/v1/daughter/access');
    if(state.access&&access.revision!==state.access.revision){state.selected=null;document.querySelector('.detail-drawer')?.remove();await load();}
  }catch(error){state.items=[];state.documents=[];state.graph=null;state.error=error.message;render();}
},15000);

setInterval(()=>{if(state.authenticated&&state.view==='dashboards'&&state.access?.identity?.acl_admin)loadMetrics();},60000);
