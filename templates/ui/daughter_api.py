"""Read-only, account-scoped UI adapter for the CLIENT daughter canon.

The existing GALACTICA API validates the HTTP-only session cookie and project
membership on every request. PostgreSQL remains the source of truth.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import posixpath
import time
from pathlib import Path
from telemetry import record
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

import psycopg
from knowledge_acl import configure_connection,identity_scope
from knowledge_acl_admin import snapshot,mutate,Conflict
from psycopg.rows import dict_row


PROJECT_ID = UUID("@@PROJECT_ID@@")
AUTH_URL = "http://pulse_reader:8062/api/galactica/data-entitlement?source=galactica%3Asfera&resource=identity"
DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)


def db():
    connection = psycopg.connect(DSN, row_factory=dict_row)
    configure_connection(connection)
    connection.execute("SET TRANSACTION READ ONLY")
    return connection


def serialize(value):
    if isinstance(value, (UUID, datetime)):
        return str(value)
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(item) for item in value]
    return value


class LoginRequired(PermissionError):pass

class KnowledgeAccessMissing(PermissionError):
    def __init__(self,identity):
        self.identity={k:identity.get(k) for k in ('username','source_subject_key')}
        super().__init__('Вход TREND выполнен, но доступ к GALACTICA CLIENT этой учётной записи не выдан.')

def source_identity(cookie):
    from standalone_identity import browser_identity
    try:return browser_identity(cookie)
    except PermissionError as exc:raise LoginRequired('Нужен вход в Сфера') from exc

def source_users(cookie):
    from standalone_identity import list_users,browser_identity
    identity=browser_identity(cookie)
    if not identity['acl_admin']:raise PermissionError('Требуются права администратора')
    return list_users()


def access_status(identity):
    with db() as connection:
        revision=connection.execute('SELECT revision FROM galactica_autonomy.acl_state').fetchone()['revision']
        sections=connection.execute('SELECT key,title,parent_key,canonical FROM galactica_autonomy.acl_sections ORDER BY title').fetchall()
        for section in sections:
            section['read']=connection.execute('SELECT galactica_autonomy.acl_can(%s,%s,%s,%s,%s) AS allowed',
                (identity['source_subject_key'],'section',section['key'],section['key'],'read')).fetchone()['allowed']
            section['manage']=connection.execute('SELECT galactica_autonomy.acl_manages(%s,%s) AS allowed',('section',section['key'])).fetchone()['allowed']
        can_admin=identity.get('acl_admin') is True or any(section['manage'] for section in sections)
    return {'identity':identity,'revision':revision,'sections':sections,'can_admin':can_admin}


def knowledge():
    with db() as connection:
        rows = connection.execute(
            """SELECT id,title,kind,area_key,classification_basis,body,memory_layer,epistemic_status,tags,
                      source_message_ids,source_spans,updated_at
               FROM galactica_autonomy.knowledge
               WHERE project_id=%s AND status='active'
               ORDER BY updated_at DESC,id""", (PROJECT_ID,)
        ).fetchall()
    return [{"id": row["id"], "title": row["title"], "content": {
        "kind": row["kind"], "area": row["area_key"],
        "classification_basis": row["classification_basis"],
        "body": row["body"], "memory_layer": row["memory_layer"],
        "epistemic_status": row["epistemic_status"], "tags": row["tags"],
        "source_message_ids": row["source_message_ids"],
        "source_spans": row["source_spans"], "updated_at": row["updated_at"],
    }} for row in rows]


def documents():
    with db() as connection:
        rows = connection.execute(
            """SELECT id,logical_key,relative_path,area_key,version,content,updated_at
               FROM galactica_autonomy.documents WHERE project_id=%s
               ORDER BY relative_path""", (PROJECT_ID,)
        ).fetchall()
    return [{"id": row["id"], "title": row["logical_key"], "content": {
        "kind": "document", "area": row["area_key"],
        "body": row["content"], "relative_path": row["relative_path"],
        "version": row["version"], "updated_at": row["updated_at"],
    }} for row in rows]


def graph(seed_id: UUID | None):
    show_all = seed_id is None
    with db() as connection:
        nodes = connection.execute(
            """SELECT id,title,kind,area_key AS area,classification_basis,
                      epistemic_status AS status,source_message_ids,source_spans
               FROM galactica_autonomy.knowledge
               WHERE project_id=%s AND status='active' ORDER BY updated_at DESC,id""", (PROJECT_ID,)
        ).fetchall()
        relations = connection.execute(
            """SELECT r.id,r.source_id,r.target_id,r.relation_type,
                      r.verification_status,cardinality(r.evidence_message_ids) AS evidence_count
               FROM galactica_autonomy.relations r
               JOIN galactica_autonomy.knowledge s ON s.id=r.source_id
               JOIN galactica_autonomy.knowledge t ON t.id=r.target_id
               WHERE s.project_id=%s AND t.project_id=%s
                 AND r.status='active' AND s.status='active' AND t.status='active'
               ORDER BY r.id""", (PROJECT_ID, PROJECT_ID)
        ).fetchall()
        messages = connection.execute(
            """SELECT m.id,m.source_system,m.observed_at
               FROM galactica_autonomy.messages m
               JOIN galactica_autonomy.tasks t ON t.id=m.task_id
               WHERE t.project_id=%s""", (PROJECT_ID,)
        ).fetchall()
    message_by_id = {row["id"]: row for row in messages}
    by_id = {row["id"]: row for row in nodes}
    neighbors = defaultdict(list)
    for edge in relations:
        if edge["source_id"] in by_id and edge["target_id"] in by_id:
            neighbors[edge["source_id"]].append(edge["target_id"])
            neighbors[edge["target_id"]].append(edge["source_id"])
    if seed_id not in by_id:
        seed_id = max(by_id, key=lambda node_id: (len(neighbors[node_id]), str(node_id))) if by_id else None
    distance = {seed_id: 0} if seed_id else {}
    queue = deque([seed_id] if seed_id else [])
    while queue and len(distance) < 100:
        current = queue.popleft()
        if distance[current] >= 2:
            continue
        for neighbor in neighbors[current]:
            if neighbor not in distance:
                distance[neighbor] = distance[current] + 1
                queue.append(neighbor)
                if len(distance) >= 100:
                    break
    area_totals = dict(Counter(row["area"] for row in nodes))
    if show_all:
        weighted_degree = defaultdict(float)
        for edge in relations:
            weight = {"source_backed": 5.0, "curated": 3.0,
                      "model_proposed": 0.25}.get(edge["verification_status"], 0.5)
            weighted_degree[edge["source_id"]] += weight
            weighted_degree[edge["target_id"]] += weight
        by_area = defaultdict(list)
        for row in nodes:
            by_area[row["area"]].append(row)
        selected = set()
        for area, candidates in by_area.items():
            candidates.sort(key=lambda row: (
                row["status"] in {"accepted_decision", "user_assertion", "verified_fact"},
                row["kind"] != "failure",
                weighted_degree[row["id"]],
                row["title"],
            ), reverse=True)
            selected.update(row["id"] for row in candidates)
    else:
        selected = set(distance)
    visible = [row for row in nodes if row["id"] in selected]
    sources = defaultdict(list)
    source_meta = {}
    for row in visible:
        for ref in row["source_message_ids"] or []:
            key = "message:" + ref
            sources[key].append(row["id"])
            meta = message_by_id.get(ref)
            source_type = ("Приложение пользователя" if meta and meta["source_system"] == "codex_attachment"
                           else "Сообщение пользователя" if meta and meta["source_system"] == "codex-notebook"
                           else "Архивное сообщение")
            source_meta[key] = {"source_type": source_type, "source_ref": ref,
                                "source_date": meta["observed_at"] if meta else None}
        for span in row["source_spans"] or []:
            if not isinstance(span, dict):
                continue
            system = span.get("source_system")
            if system == "galactica_parent":
                project = span.get("source_project_id") or "unknown"
                key = "parent:" + project
                names = {
                    "cb8fc3c9-16cf-4b22-b22b-4fe2ed62b20b": "Эксперты и навыки",
                    "840f6494-accc-5a59-ace8-63d654e3ed0a": "Контент и дизайн",
                    "594240fa-6c2a-4234-8c84-b7fe01859258": "Метрики и аналитика",
                    "679e276e-69bc-4c8c-9c54-fb10699f7f52": "Marketplace API Connections",
                    "b2b19bde-914d-46c1-8fbc-e78e1ee3545e": "Ozon: продвижение",
                    "d91e3dd2-1776-46ca-b176-f89360899a74": "Wildberries: ранжирование",
                }
                source_meta[key] = {"source_type": "Материнская GALACTICA",
                                    "source_ref": project, "source_date": None,
                                    "title": "Материнская · " + names.get(project, project)}
            elif system == "trend_runtime":
                locator = span.get("source_locator") or "trend-runtime"
                key = "trend:" + locator
                source_meta[key] = {"source_type": "Работающий ТРЕНД", "source_ref": locator,
                                    "source_date": None, "title": "ТРЕНД · React CSS"}
            elif system == "codex_local":
                locator = span.get("source_locator") or ""
                if not locator.startswith("codex://threads/"):
                    continue
                key = "codex:" + locator
                date = span.get("source_date") or ""
                latest = source_meta.get(key, {}).get("source_date") or ""
                source_meta[key] = {"source_type": "Локальный тред Codex",
                                    "source_ref": locator.removeprefix("codex://threads/"),
                                    "source_date": max(date, latest) or None,
                                    "title": "Codex · " + (span.get("source_title") or "Задача CLIENT")}
            else:
                continue
            sources[key].append(row["id"])
    source_nodes = []
    provenance_edges = []
    for index, (key, member_ids) in enumerate(sorted(sources.items())):
        source_id = f"source:{index}"
        meta = source_meta[key]
        source_type = meta["source_type"]
        title = meta.get("title") or ("Приложение к задаче CLIENT" if source_type == "Приложение пользователя"
                                      else f"Запрос: {by_id[member_ids[0]]['title']}" if len(member_ids) == 1
                                      else f"Сообщение пользователя · {len(member_ids)} знаний")
        source_nodes.append({"id": source_id, "title": title,
                             "kind": "source", "status": "active", "distance": None,
                             "source_count": len(member_ids), "source_type": source_type,
                             "source_ref": meta["source_ref"], "source_date": meta["source_date"]})
        provenance_edges.extend({"id": f"provenance:{index}:{member_id}",
                                 "source_item_id": source_id, "target_item_id": member_id,
                                 "relation_type": "ИСТОЧНИК", "edge_kind": "provenance"}
                                for member_id in member_ids)
    semantic_edges = [{"id": row["id"], "source_item_id": row["source_id"],
                       "target_item_id": row["target_id"], "relation_type": row["relation_type"],
                       "verification_status": row["verification_status"],
                       "evidence_count": row["evidence_count"], "edge_kind": "semantic"}
                      for row in relations if row["source_id"] in selected and row["target_id"] in selected]
    with db() as connection:
        area_rows = connection.execute(
            """SELECT key,title,description FROM galactica_autonomy.entity_areas
               ORDER BY display_order"""
        ).fetchall()
    members_by_area = defaultdict(list)
    for row in visible:
        members_by_area[row["area"]].append(row["id"])
    area_nodes = [{"id": f"area:{area['key']}", "title": area["title"],
                   "kind": "area", "area": area["key"],
                   "description": area["description"],
                   "member_count": area_totals[area["key"]],
                   "visible_count": len(members_by_area[area["key"]])}
                  for area in area_rows if members_by_area[area["key"]]]
    membership_edges = [{"id": f"membership:{area}:{member_id}",
                         "source_item_id": f"area:{area}", "target_item_id": member_id,
                         "relation_type": "В ОБЛАСТИ", "edge_kind": "membership"}
                        for area, member_ids in members_by_area.items()
                        for member_id in member_ids]
    return {"project_id": PROJECT_ID,
            "nodes": [{**row, "distance": distance.get(row["id"])} for row in visible]
                     + area_nodes + source_nodes,
            "edges": semantic_edges + membership_edges + provenance_edges,
            "knowledge_count": len(visible), "total_knowledge_count": len(nodes),
            "area_summary": area_totals,
            "semantic_edge_count": len(semantic_edges),
            "area_count": len(area_nodes), "source_count": len(source_nodes),
            "scope": "overview" if show_all else "neighborhood",
            "truncated": len(selected) < len(by_id) if show_all else len(distance) >= 100}


def health():
    with db() as connection:
        row = connection.execute(
            """SELECT (SELECT count(*) FROM galactica_autonomy.knowledge
                          WHERE project_id=%s AND status='active') AS node_count,
                      (SELECT count(*) FROM galactica_autonomy.relations r
                       JOIN galactica_autonomy.knowledge s ON s.id=r.source_id
                       JOIN galactica_autonomy.knowledge t ON t.id=r.target_id
                       WHERE s.project_id=%s AND t.project_id=%s AND r.status='active'
                         AND s.status='active' AND t.status='active') AS edge_count,
                      (SELECT count(*) FROM galactica_autonomy.documents
                          WHERE project_id=%s) AS document_count,
                      (SELECT count(*) FROM galactica_autonomy.tasks
                          WHERE project_id=%s) AS task_count,
                      (SELECT count(*) FROM galactica_autonomy.messages m
                       JOIN galactica_autonomy.tasks t ON t.id=m.task_id
                          WHERE t.project_id=%s) AS message_count,
                      (SELECT count(*) FROM galactica_autonomy.skill_candidates
                          WHERE status='active') AS skill_count""",
            (PROJECT_ID, PROJECT_ID, PROJECT_ID, PROJECT_ID, PROJECT_ID, PROJECT_ID),
        ).fetchone()
        skills = connection.execute(
            """SELECT key,title,scope,version FROM galactica_autonomy.skill_candidates
               WHERE status='active' ORDER BY title LIMIT 30"""
        ).fetchall()
    return {**row, "skills": skills}


def complete_graph():
    result=graph(None)
    with db() as connection:
        area_titles={row['key']:row['title'] for row in connection.execute('SELECT key,title FROM galactica_autonomy.entity_areas').fetchall()}
        docs=connection.execute("SELECT id,logical_key,area_key,relative_path,content FROM galactica_autonomy.documents WHERE project_id=%s",(PROJECT_ID,)).fetchall()
        skills=connection.execute("SELECT key,title FROM galactica_autonomy.skill_candidates WHERE status='active'").fetchall()
        tasks=connection.execute("SELECT id,request_text,status FROM galactica_autonomy.tasks WHERE project_id=%s",(PROJECT_ID,)).fetchall()
        messages=connection.execute("SELECT m.id,m.task_id FROM galactica_autonomy.messages m JOIN galactica_autonomy.tasks t ON t.id=m.task_id WHERE t.project_id=%s",(PROJECT_ID,)).fetchall()
        edits=connection.execute("SELECT DISTINCT v.document_id,v.task_id FROM galactica_autonomy.document_versions v JOIN galactica_autonomy.documents d ON d.id=v.document_id JOIN galactica_autonomy.tasks t ON t.id=v.task_id WHERE d.project_id=%s AND t.project_id=%s",(PROJECT_ID,PROJECT_ID)).fetchall()
    result['nodes'].extend({'id':'document:'+str(d['id']),'title':d['logical_key'],'kind':'document','area':d['area_key']} for d in docs)
    result['nodes'].extend({'id':'skill:'+d['key'],'title':d['title'],'kind':'skill','area':'skills'} for d in skills)
    result['nodes'].extend({'id':'task:'+str(t['id']),'title':t['request_text'][:180],'kind':'task','area':'work','status':t['status']} for t in tasks)
    area_ids={n['id'] for n in result['nodes'] if n['kind']=='area'}
    for node in result['nodes']:
        if node['kind'] in ['document','task','skill']:
            area='area:'+str(node.get('area') or 'knowledge')
            if area not in area_ids:
                area_ids.add(area);result['nodes'].append({'id':area,'title':area_titles.get(node.get('area'),'Знания'),'kind':'area','area':node.get('area') or 'knowledge'})
            result['edges'].append({'id':'membership:'+node['id'],'source_item_id':area,'target_item_id':node['id'],'edge_kind':'membership','relation_type':'В ОБЛАСТИ'})
    visible_tasks={str(t['id']) for t in tasks}
    message_task={m['id']:str(m['task_id']) for m in messages}
    pages={d['relative_path']:'document:'+str(d['id']) for d in docs}
    reference_pairs=set()
    for doc in docs:
        for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)',doc['content']):
            if '://' in link or link.startswith('#'):continue
            target=posixpath.normpath(posixpath.join(posixpath.dirname(doc['relative_path']),link.split('#')[0]))
            if target in pages:reference_pairs.add(('document:'+str(doc['id']),pages[target]))
    for source,target in sorted(reference_pairs):
        result['edges'].append({'id':'page-reference:'+source+':'+target,'source_item_id':source,'target_item_id':target,'edge_kind':'reference','relation_type':'ССЫЛКА СТРАНИЦЫ'})
    for edit in edits:
        result['edges'].append({'id':'document-edit:'+str(edit['task_id'])+':'+str(edit['document_id']),'source_item_id':'task:'+str(edit['task_id']),'target_item_id':'document:'+str(edit['document_id']),'edge_kind':'provenance','relation_type':'ИЗМЕНЕНИЕ СТРАНИЦЫ'})
    for node in result['nodes']:
        linked=set()
        for ref in node.get('source_message_ids') or []:
            if ref in message_task:linked.add(message_task[ref])
        for span in node.get('source_spans') or []:
            if isinstance(span,dict) and str(span.get('task_id')) in visible_tasks:linked.add(str(span['task_id']))
        for task in linked:
            result['edges'].append({'id':'task-origin:'+task+':'+str(node['id']),'source_item_id':'task:'+task,'target_item_id':node['id'],'edge_kind':'provenance','relation_type':'ИТОГ ЗАДАЧИ'})
    result['scope']='complete'
    result['truncated']=False
    return result


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code: int, value) -> None:
        if self.path!='/healthz':record(duration_ms=(time.monotonic()-getattr(self,'_request_start',time.monotonic()))*1000,error=code>=400)
        body = json.dumps(serialize(value), ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._request_start=time.monotonic()
        path=urlsplit(self.path)
        if path.path=='/healthz':
            self.send_json(200,{'status':'ok'});return
        if path.path=='/readyz':
            try:
                with db() as connection:
                    connection.execute('SELECT 1 FROM galactica_autonomy.documents LIMIT 1')
                self.send_json(200,{'status':'ok'})
            except Exception:
                self.send_json(503,{'status':'unavailable'})
            return
        try:
            cookie=self.headers.get('Cookie','')
            identity=source_identity(cookie)
            with identity_scope(identity['source_subject_key'],identity.get('acl_admin')):
                before=access_status(identity)
                if path.path=='/v1/daughter/knowledge':result=knowledge()
                elif path.path=='/v1/daughter/documents':result=documents()
                elif path.path=='/v1/daughter/graph':
                    seed=parse_qs(path.query).get('seed_id',[None])[0]
                    result=complete_graph() if not seed else graph(UUID(seed))
                elif path.path=='/v1/daughter/metrics':
                    if not identity.get('acl_admin'):raise PermissionError('Метрики доступны администратору GALACTICA')
                    result={'status':'available','scope':'instance','source':'sfera','note':'Host monitoring is optional'}
                elif path.path=='/v1/daughter/health':result=health()
                elif path.path=='/v1/daughter/access':result=before
                elif path.path=='/v1/daughter/admin/access':
                    users=source_users(cookie) if identity.get('acl_admin') else []
                    with db() as connection:result=snapshot(connection,identity,users)
                else:
                    self.send_json(404,{'detail':'Not found'});return
                if access_status(identity)['revision']!=before['revision']:
                    self.send_json(409,{'detail':'Права изменились. Повтори запрос.'});return
                # Recheck source identity/current permissions before delivering any payload.
                final=source_identity(cookie)
                if (final['source_subject_key']!=identity['source_subject_key'] or final.get('acl_admin')!=identity.get('acl_admin')):
                    raise PermissionError('Права TREND изменились')
            self.send_json(200,result)
        except KnowledgeAccessMissing as error:self.send_json(403,{'detail':str(error),'reason_code':'GALACTICA_ACCESS_MISSING','identity':error.identity})
        except LoginRequired as error:self.send_json(401,{'detail':str(error)})
        except PermissionError as error:self.send_json(403,{'detail':str(error)})
        except ValueError:self.send_json(422,{'detail':'Некорректный запрос'})
        except Exception:self.send_json(503,{'detail':'Данные GALACTICA временно недоступны'})

    def do_POST(self) -> None:
        self._request_start=time.monotonic()
        try:
            if urlsplit(self.path).path!='/v1/daughter/admin/access':
                self.send_json(404,{'detail':'Not found'});return
            if self.headers.get('Origin')!=os.environ['SFERA_ORIGIN']:
                raise PermissionError('Недопустимый источник запроса')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=24000:raise ValueError('Слишком большой запрос')
            payload=json.loads(self.rfile.read(length))
            identity=source_identity(self.headers.get('Cookie',''))
            with identity_scope(identity['source_subject_key'],identity.get('acl_admin')):
                with psycopg.connect(DSN,row_factory=dict_row) as connection:
                    configure_connection(connection)
                    result=mutate(connection,identity,payload)
                    final=source_identity(self.headers.get('Cookie',''))
                    if final['source_subject_key']!=identity['source_subject_key'] or final.get('acl_admin')!=identity.get('acl_admin'):
                        raise PermissionError('Source access changed')
            self.send_json(200,result)
        except Conflict as error:self.send_json(409,{'detail':str(error)})
        except KnowledgeAccessMissing as error:self.send_json(403,{'detail':str(error),'reason_code':'GALACTICA_ACCESS_MISSING','identity':error.identity})
        except LoginRequired as error:self.send_json(401,{'detail':str(error)})
        except PermissionError as error:self.send_json(403,{'detail':str(error)})
        except (ValueError,TypeError,KeyError):self.send_json(422,{'detail':'Проверь введённые значения'})
        except Exception:self.send_json(503,{'detail':'Изменение прав временно недоступно'})

    def log_message(self, format: str, *args) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
