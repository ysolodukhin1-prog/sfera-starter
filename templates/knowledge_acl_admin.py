"""Small administration API: all operations use verified source identity and CAS."""
import re,json
from uuid import UUID,uuid4
from psycopg.types.json import Jsonb
from knowledge_acl import require_permission

class Conflict(ValueError):pass

def owner(identity):
    if identity.get('acl_admin') is not True:raise PermissionError('Управление разделами доступно администратору GALACTICA')

def manages(db,scope_kind,key):
    row=db.execute('SELECT galactica_autonomy.acl_manages(%s,%s) AS allowed',(scope_kind,key)).fetchone()
    if not row['allowed']:raise PermissionError('Нет права управлять доступом к этому разделу')

def text(value,limit=120):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('Некорректное значение')
    return value.strip()

def validate_material(db,kind,key):
    if kind in {'knowledge','document'}:
        UUID(key)
        table={'knowledge':'knowledge','document':'documents'}[kind]
        found=db.execute(f"SELECT 1 FROM galactica_autonomy.{table} WHERE id=%s AND project_id='@@PROJECT_ID@@'",(key,)).fetchone()
    elif kind=='skill':
        found=db.execute('SELECT 1 FROM galactica_autonomy.skill_candidates WHERE key=%s',(key,)).fetchone()
    else:raise ValueError('Неизвестный материал')
    if not found:raise ValueError('Материал не найден или недоступен')

def snapshot(db,identity,users):
    sections=db.execute('SELECT key,title,parent_key,canonical FROM galactica_autonomy.acl_sections ORDER BY title').fetchall()
    can_admin=identity.get('acl_admin') is True or any(db.execute('SELECT galactica_autonomy.acl_manages(%s,%s) AS allowed',('section',s['key'])).fetchone()['allowed'] for s in sections)
    if not can_admin:raise PermissionError('Администрирование недоступно')
    materials=db.execute("""SELECT 'knowledge' AS kind,id::text AS key,title,area_key FROM galactica_autonomy.knowledge WHERE project_id='@@PROJECT_ID@@' AND status='active'
        UNION ALL SELECT 'document',id::text,logical_key,area_key FROM galactica_autonomy.documents WHERE project_id='@@PROJECT_ID@@'
        UNION ALL SELECT 'skill',key,title,'skills' FROM galactica_autonomy.skill_candidates WHERE status IN ('active','candidate','proposed')""").fetchall()
    rules=db.execute('SELECT * FROM galactica_autonomy.acl_rules ORDER BY scope_kind,scope_key,subject_kind,subject_key,action').fetchall()
    groups=db.execute('SELECT * FROM galactica_autonomy.acl_groups ORDER BY title').fetchall()
    members=db.execute('SELECT * FROM galactica_autonomy.acl_group_members ORDER BY group_id,subject').fetchall()
    assignments=db.execute('SELECT * FROM galactica_autonomy.acl_material_sections').fetchall()
    audit=db.execute('SELECT id,revision,actor,operation,before_value,after_value,created_at FROM galactica_autonomy.acl_audit ORDER BY id DESC LIMIT 100').fetchall()
    revision=db.execute('SELECT revision FROM galactica_autonomy.acl_state').fetchone()['revision']
    for s in sections:
        s['permissions']={a:db.execute('SELECT galactica_autonomy.acl_can(%s,%s,%s,%s,%s) AS allowed',(identity['source_subject_key'],'section',s['key'],s['key'],a)).fetchone()['allowed'] for a in ('read','create','edit','manage')}
    return dict(revision=revision,identity=identity,users=users,sections=sections,materials=materials,rules=rules,groups=groups,members=members,assignments=assignments,audit=audit)

def mutate(db,identity,payload):
    if not isinstance(payload,dict) or len(json.dumps(payload))>20000:raise ValueError('Некорректный запрос')
    expected=payload.get('expected_revision')
    if type(expected) is not int:raise ValueError('Нужна текущая версия прав')
    revision=db.execute('SELECT revision FROM galactica_autonomy.acl_state FOR UPDATE').fetchone()['revision']
    if revision!=expected:raise Conflict('Права уже изменились. Обнови страницу перед сохранением.')
    op=payload.get('operation');before=None;after=None
    if op=='rules':
        scope_kind=payload.get('scope_kind');key=text(payload.get('scope_key'),200)
        if scope_kind not in {'root','section','material'}:raise ValueError('Неизвестная область')
        if scope_kind=='root' and key!='*':raise ValueError('Неизвестный контур')
        if scope_kind=='section' and not db.execute('SELECT 1 FROM galactica_autonomy.acl_sections WHERE key=%s',(key,)).fetchone():raise ValueError('Раздел не найден')
        if scope_kind=='material':
            kind,_,resource=key.partition(':')
            validate_material(db,kind,resource)
        manages(db,scope_kind,key)
        subject_kind=payload.get('subject_kind');subject=text(payload.get('subject_key'))
        if subject_kind!='user':raise ValueError('Права назначаются только отдельному пользователю Сферы')
        UUID(subject)
        from standalone_identity import list_users
        if not any(u['source_subject_key']==subject for u in list_users()):raise ValueError('Unknown Sfera user')
        actions=payload.get('actions')
        if not isinstance(actions,dict) or set(actions)!={'read','create','edit','manage'} or any(v not in {'inherit','allow','deny'} for v in actions.values()):raise ValueError('Некорректные права')
        before=db.execute('SELECT * FROM galactica_autonomy.acl_rules WHERE scope_kind=%s AND scope_key=%s AND subject_kind=%s AND subject_key=%s',(scope_kind,key,subject_kind,subject)).fetchall()
        for action,effect in actions.items():
            params=(scope_kind,key,subject_kind,subject,action)
            if effect=='inherit':db.execute('DELETE FROM galactica_autonomy.acl_rules WHERE scope_kind=%s AND scope_key=%s AND subject_kind=%s AND subject_key=%s AND action=%s',params)
            else:db.execute('''INSERT INTO galactica_autonomy.acl_rules(scope_kind,scope_key,subject_kind,subject_key,action,effect) VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(scope_kind,scope_key,subject_kind,subject_key,action) DO UPDATE SET effect=EXCLUDED.effect''',(*params,effect))
        after={'scope_kind':scope_kind,'scope_key':key,'subject_kind':subject_kind,'subject_key':subject,'actions':actions}
    elif op in {'group_create','group_members','group_delete'}:
        raise ValueError('Группы не используются. Назначь персональные права пользователю Сферы')
    elif op=='section_create':
        owner(identity);parent=text(payload.get('parent_key'))
        if not db.execute('SELECT 1 FROM galactica_autonomy.acl_sections WHERE key=%s',(parent,)).fetchone():raise ValueError('Родительский раздел не найден')
        depth=db.execute("WITH RECURSIVE ancestors AS (SELECT key,parent_key,0 AS depth FROM galactica_autonomy.acl_sections WHERE key=%s UNION ALL SELECT s.key,s.parent_key,a.depth+1 FROM galactica_autonomy.acl_sections s JOIN ancestors a ON s.key=a.parent_key) SELECT max(depth) AS depth FROM ancestors",(parent,)).fetchone()['depth']
        if depth>=15:raise ValueError('Достигнут предел вложенности разделов')
        after=db.execute('INSERT INTO galactica_autonomy.acl_sections(key,title,parent_key) VALUES(%s,%s,%s) RETURNING *',('section-'+uuid4().hex,text(payload.get('title')),parent)).fetchone()
    elif op=='material_move':
        kind=payload.get('kind');key=text(payload.get('key'),180);section=text(payload.get('section_key'))
        validate_material(db,kind,key)
        manages(db,'material',str(kind)+':'+key);manages(db,'section',section)
        if not db.execute('SELECT 1 FROM galactica_autonomy.acl_sections WHERE key=%s',(section,)).fetchone():raise ValueError('Раздел не найден')
        before=db.execute('SELECT * FROM galactica_autonomy.acl_material_sections WHERE kind=%s AND resource_key=%s',(kind,key)).fetchone()
        after=db.execute('''INSERT INTO galactica_autonomy.acl_material_sections(kind,resource_key,section_key) VALUES(%s,%s,%s)
            ON CONFLICT(kind,resource_key) DO UPDATE SET section_key=EXCLUDED.section_key RETURNING *''',(kind,key,section)).fetchone()
    else:raise ValueError('Неизвестное действие')
    def safe(v):return json.loads(json.dumps(v,default=str))
    db.execute('UPDATE galactica_autonomy.acl_state SET revision=revision+1')
    db.execute('INSERT INTO galactica_autonomy.acl_audit(revision,actor,operation,before_value,after_value) VALUES(%s,%s,%s,%s,%s)',
               (revision+1,identity['source_subject_key'],op,Jsonb(safe(before)),Jsonb(safe(after))))
    return {'saved':True,'revision':revision+1,'result':after}
