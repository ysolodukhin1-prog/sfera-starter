/* Knowledge access administration. Identity and permissions are verified server-side. */
const aclState={data:null,loading:false,error:'',saved:false,tab:'rules',scopeKind:'root',scopeKey:'*',subjectKey:''};
const ACL_ACTIONS={read:'Читать',create:'Создавать',edit:'Редактировать',manage:'Управлять доступом'};

async function loadAccessAdmin(){
  aclState.loading=true;aclState.error='';render();
  try{aclState.data=await request('/v1/daughter/admin/access');}
  catch(error){aclState.error=error.message;}
  aclState.loading=false;render();
}

async function saveAccess(change){
  try{
    aclState.saved=false;
    await request('/v1/daughter/admin/access',{method:'POST',body:JSON.stringify({...change,expected_revision:aclState.data.revision})});
    await loadAccessAdmin();
    const access=await request('/v1/daughter/access');state.access=access;aclState.saved=true;render();
  }catch(error){aclState.error=error.message;render();}
}

function aclOptions(items,value,key='key',label='title'){
  return items.map(item=>`<option value="${escapeHtml(item[key])}" ${String(item[key])===String(value)?'selected':''}>${escapeHtml(item[label])}</option>`).join('');
}

function accessAdminView(){
  const data=aclState.data;
  const tabs=[['rules','Права пользователей'],['sections','Разделы'],['audit','Журнал']];
  const intro=heading('АДМИНИСТРИРОВАНИЕ','Админка','Персональные права на разделы и материалы. Выбери пользователя Сфера и нужную область.');
  if(!data)return shell(`${intro}<section class="panel acl-panel">${aclState.loading?'Загрузка прав…':escapeHtml(aclState.error||'Открой настройки доступа.')}</section>`);
  const materials=data.materials.map(m=>({...m,key:`${m.kind}:${m.key}`,title:`${m.kind==='document'?'Документ':m.kind==='skill'?'Навык':'Знание'} · ${m.title}`}));
  const owner=data.identity.acl_admin===true;
  let content='';
  if(aclState.tab==='rules'){
    const targets=aclState.scopeKind==='section'?data.sections:aclState.scopeKind==='material'?materials:[{key:'*',title:'Весь Сфера · CLIENT'}];
    if(!targets.some(x=>x.key===aclState.scopeKey))aclState.scopeKey=targets[0]?.key||'';
    const users=data.users.map(u=>({key:String(u.user_id),title:`${u.display_name||u.username} · ${u.username}${u.is_active?'':' · отключён'}`}));
    if(users.length&&!users.some(x=>x.key===aclState.subjectKey))aclState.subjectKey=users[0].key;
    const rules=data.rules.filter(r=>r.scope_kind===aclState.scopeKind&&r.scope_key===aclState.scopeKey&&r.subject_kind==='user'&&r.subject_key===aclState.subjectKey);
    content=`<section class="panel acl-panel"><h2>Персональные права</h2><form id="acl-rule-form" class="acl-form">
      <label style="grid-column:1/-1">1. Пользователь Сфера<select id="acl-subject-key" required>${users.length?aclOptions(users,aclState.subjectKey):'<option value="">Нет доступных пользователей</option>'}</select></label>
      <label>2. Где действуют права<select id="acl-scope-kind">${aclOptions([{key:'root',title:'Весь Сфера'},{key:'section',title:'Раздел или подраздел'},{key:'material',title:'Отдельный материал'}],aclState.scopeKind)}</select></label>
      <label>Раздел или материал<select id="acl-scope-key">${aclOptions(targets,aclState.scopeKey)}</select></label>
      <div class="acl-actions">${Object.entries(ACL_ACTIONS).map(([key,label])=>{const effect=rules.find(r=>r.action===key)?.effect||'inherit';return `<label>${label}<select name="${key}">${aclOptions([{key:'inherit',title:'Наследовать'},{key:'allow',title:'Разрешить'},{key:'deny',title:'Запретить'}],effect)}</select></label>`;}).join('')}</div>
      <button class="primary-button" type="submit" ${users.length?'':'disabled'}>Сохранить права пользователя</button>
    </form><div class="acl-help"><strong>Как работает наследование</strong><p>Каждое изменение сохраняется только для выбранного пользователя. «Наследовать» убирает его отдельное правило здесь: действует правило вышестоящего раздела, затем начальные права системы. Правило материала важнее правила раздела, а подраздела — важнее родителя. «Запретить» закрывает выбранное действие в этой области.</p><p>Доступ определяется пользователем и разрешениями этого экземпляра Сферы. Создание и редактирование применяются к доступным операциям помощника: задачам и Markdown. Администратор Сферы сохраняет доступ к настройкам.</p></div></section>`;
  }else if(aclState.tab==='sections'){
    const options=aclOptions(data.sections,'');
    content=`<section class="panel acl-panel"><h2>Подразделы знаний</h2><p>Подразделы наследуют правила родителя. При назначении материала его каноническая классификация сохраняется.</p>${owner?`<form id="acl-section-create" class="acl-inline-form"><input name="title" placeholder="Название подраздела" maxlength="120" required><select name="parent_key">${options}</select><button class="primary-button">Создать подраздел</button></form>`:''}
      <form id="acl-material-move" class="acl-form"><label>Материал<select name="material">${aclOptions(materials,'')}</select></label><label>Раздел доступа<select name="section_key">${options}</select></label><button class="primary-button">Назначить раздел</button></form>
      <div class="acl-section-list">${data.sections.map(s=>`<div><strong>${escapeHtml(s.title)}</strong><small>${s.parent_key?'Наследует: '+escapeHtml(data.sections.find(p=>p.key===s.parent_key)?.title||s.parent_key):'Основной раздел'}</small></div>`).join('')}</div></section>`;
  }else{
    const label={rules:'Изменение прав',group_create:'Создание группы',group_members:'Состав группы',group_delete:'Удаление группы',section_create:'Создание подраздела',material_move:'Раздел материала'};
    const name=subject=>subject==='admin'?'Администратор Сфера':data.users.find(u=>String(u.user_id)===subject)?.username||`Сфера ID ${subject}`;
    content=`<section class="panel acl-panel"><h2>Журнал изменений</h2><p>Кто изменил права, когда и с какой версии. Записи журнала не редактируются.</p><div class="acl-audit">${data.audit.map(a=>`<article><strong>${escapeHtml(label[a.operation]||a.operation)}</strong><small>${escapeHtml(name(a.actor))} · ${escapeHtml(new Date(a.created_at).toLocaleString('ru-RU'))} · версия ${a.revision}</small><details><summary>Изменение</summary><pre>${escapeHtml(JSON.stringify({до:a.before_value,после:a.after_value},null,2))}</pre></details></article>`).join('')||'<p>Изменений ещё нет.</p>'}</div></section>`;
  }
  return shell(`${intro}<div class="acl-tabs" role="tablist" aria-label="Настройки доступа">${tabs.map(([key,label])=>`<button role="tab" aria-selected="${aclState.tab===key}" data-acl-tab="${key}" class="${aclState.tab===key?'active':''}">${label}</button>`).join('')}<span>Версия ${data.revision}</span></div>${aclState.saved?'<div class="acl-success" role="status">Изменения сохранены. Новые права действуют при следующем запросе.</div>':''}${aclState.error?`<p role="alert" class="acl-error">${escapeHtml(aclState.error)}</p>`:''}${content}`);
}

function bindAccessAdmin(){
  app.querySelectorAll('[data-acl-tab]').forEach(b=>b.addEventListener('click',()=>{aclState.tab=b.dataset.aclTab;render();}));
  for(const [id,key] of [['acl-scope-kind','scopeKind'],['acl-scope-key','scopeKey'],['acl-subject-key','subjectKey']]){
    document.getElementById(id)?.addEventListener('change',e=>{aclState[key]=e.target.value;render();});
  }
  document.getElementById('acl-rule-form')?.addEventListener('submit',e=>{e.preventDefault();const form=new FormData(e.target);saveAccess({operation:'rules',scope_kind:aclState.scopeKind,scope_key:aclState.scopeKey,subject_kind:'user',subject_key:document.getElementById('acl-subject-key').value,actions:Object.fromEntries(Object.keys(ACL_ACTIONS).map(a=>[a,form.get(a)]))});});
  document.getElementById('acl-section-create')?.addEventListener('submit',e=>{e.preventDefault();saveAccess({operation:'section_create',...Object.fromEntries(new FormData(e.target))});});
  document.getElementById('acl-material-move')?.addEventListener('submit',e=>{e.preventDefault();const form=new FormData(e.target);const value=form.get('material');const split=value.indexOf(':');saveAccess({operation:'material_move',kind:value.slice(0,split),key:value.slice(split+1),section_key:form.get('section_key')});});
}
