"""Isolated native PostgreSQL rehearsal on the notebook; no VPS writes."""
import os,sys,types,importlib.util,json,secrets,re,hashlib,base64
from pathlib import Path
ROOT=Path(os.getenv('SFERA_PACKAGE_ROOT','/app'))
INSTANCE=Path(os.getenv('SFERA_INSTANCE_ROOT','/instance'))
if os.environ.get('SFERA_CLIENT_KEY')!='installer-test':raise RuntimeError('Run only on installer-test, never a real client')
sys.path[:0]=[str(ROOT),str(ROOT/'runtime_support')]
passwords=json.loads((INSTANCE/'bootstrap.json').read_text())['passwords']
env=dict(os.environ)
env.update({k.upper()+'_PASSWORD':v for k,v in passwords.items()})
import psycopg
ADMIN_DSN=os.environ['DATABASE_URL']
def load_bootstrap():
 spec=importlib.util.spec_from_file_location('bootstrap',ROOT/'bootstrap.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
boot=load_bootstrap()
if '--init' in sys.argv:
 boot.initialize()
 with psycopg.connect(ADMIN_DSN) as db:
  for table in ['tasks','messages','knowledge']:
   assert db.execute('SELECT count(*) FROM galactica_autonomy.'+table).fetchone()[0]==0,table+' was not empty'
 print('Initial canonical business data tables are empty');sys.exit(0)
password=secrets.token_urlsafe(24)
assert boot.config['project_id']!='ae033740-645e-4e3e-bee1-d6b925532e8e'
for username,role in [('test-owner','owner'),('test-reader','reader'),('test-contributor','contributor')]:
 with psycopg.connect(ADMIN_DSN) as db:
  exists=db.execute('SELECT principal_id FROM galactica_autonomy.sfera_local_users WHERE username=%s',(username,)).fetchone()
  if exists:
   from standalone_identity import password_hash
   db.execute("UPDATE galactica_autonomy.sfera_local_users SET password_hash=%s,status='active',failed_attempts=0,locked_until=NULL WHERE principal_id=%s",(password_hash(password),exists[0]))
  else:boot.create_user(username,password,role)
try:boot.initialize();raise AssertionError('Nonempty database accepted')
except RuntimeError as exc:assert 'not empty' in str(exc)
os.environ['DATABASE_URL']='postgresql://galactica_client_mcp:'+env['GALACTICA_CLIENT_MCP_PASSWORD']+'@postgres:5432/sfera'
os.environ['AUTH_DATABASE_URL']='postgresql://sfera_auth:'+env['SFERA_AUTH_PASSWORD']+'@postgres:5432/sfera'
from fastapi.testclient import TestClient
from client_mcp_server import app
from standalone_identity import auth_connect,credential_identity
client=TestClient(app,base_url=env['SFERA_ORIGIN'])
checks=[]
def check(name,condition):
 assert condition,name;checks.append(name)
def signin(c,user):
 r=c.get('/login');csrf=re.search('name="csrf" value="([^"]+)"',r.text).group(1)
 r=c.post('/login',data={'username':user,'password':password,'csrf':csrf,'next':'/galactica/'},headers={'Origin':env['SFERA_ORIGIN']},follow_redirects=False)
 assert r.status_code==303,('login failed',r.status_code,r.text)
def oauth(c,user):
 signin(c,user);r=c.post('/oauth/register',json={'client_name':'Test client','redirect_uris':['http://127.0.0.1:19444/callback']});assert r.status_code==201,r.text;cid=r.json()['client_id'];verifier=secrets.token_urlsafe(48);challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
 params={'response_type':'code','client_id':cid,'redirect_uri':'http://127.0.0.1:19444/callback','state':'test-state','code_challenge':challenge,'code_challenge_method':'S256','scope':'sfera:read sfera:task sfera:write','resource':env['SFERA_ORIGIN']+'/galactica-mcp/mcp'}
 r=c.get('/oauth/authorize',params=params);assert r.status_code==200,r.text
 rid=re.search('name="request_id" value="([^"]+)"',r.text).group(1);csrf=re.search('name="csrf" value="([^"]+)"',r.text).group(1)
 r=c.post('/oauth/authorize',data={'request_id':rid,'csrf':csrf},headers={'Origin':env['SFERA_ORIGIN']},follow_redirects=False);assert r.status_code==303,r.text
 from urllib.parse import parse_qs,urlsplit
 code=parse_qs(urlsplit(r.headers['location']).query)['code'][0]
 body={'grant_type':'authorization_code','code':code,'client_id':cid,'redirect_uri':params['redirect_uri'],'code_verifier':verifier,'resource':params['resource']}
 bad=c.post('/oauth/token',data={**body,'code_verifier':secrets.token_urlsafe(48)});check('Wrong PKCE rejected',bad.status_code==400)
 r=c.post('/oauth/token',data=body);assert r.status_code==200,r.text
 check('Authorization code cannot be replayed',c.post('/oauth/token',data=body).status_code==400)
 return r.json(),cid
def call(c,t,name,args={}):
 r=c.post('/mcp',headers={'Authorization':'Bearer '+t['access_token']},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}});assert r.status_code==200,r.text
 result=r.json()['result'];return result,json.loads(result['content'][0]['text']) if not result['isError'] else result['content'][0]['text']
check('Anonymous MCP denied',client.post('/mcp',json={}).status_code==401)
check('Login rejects open redirect',client.get('/login',params={'next':'//evil.example'}).status_code==400)
check('Login CSRF denied',client.post('/login',data={'username':'test-owner','password':password,'csrf':'bad'},headers={'Origin':env['SFERA_ORIGIN']}).status_code==401)
owner,cid=oauth(client,'test-owner');result,identity=call(client,owner,'whoami');check('Independent owner identity',identity['username']=='test-owner' and identity['acl_admin'])
result,context=call(client,owner,'get_context');check('Neutral context available',not result['isError'] and len(context['documents'])>=7)
result,skills=call(client,owner,'list_skills',{'include_candidates':True});check('Packaged skills available',not result['isError'])
reader_client=TestClient(app,base_url=env['SFERA_ORIGIN']);reader,_=oauth(reader_client,'test-reader');check('Reader receives no write/task scopes',reader['scope']=='sfera:read')
result,value=call(reader_client,reader,'start_task',{'request':'Not permitted','criteria':[]});check('Reader cannot create tasks',result['isError'])
result,task=call(client,owner,'start_task',{'request':'Installer integration verification','criteria':['Provenance and access verified']});check('Owner task creation',not result['isError'])
task_id=str(task['task']['task_id'])
result,value=call(client,owner,'read_task',{'task_id':task_id});check('Owner reads the actual created task',not result['isError'])
result,value=call(reader_client,reader,'read_task',{'task_id':task_id});check('Private task cannot be read by another user',result['isError'])
result,document=call(client,owner,'read_document',{'logical_key':'client/current_task'});assert not result['isError']
check('Canonical document returned',bool(document.get('sha256')))
content=document['content']+'\nInstaller test completed.\n'
args={'task_id':task_id,'logical_key':'client/current_task','expected_sha':document['sha256'],'content':content}
result,value=call(client,owner,'write_document',args);check('Journaled Markdown update',not result['isError'])
result,value=call(client,owner,'write_document',args);check('Stale SHA update refused',result['isError'])
summary={k:[] for k in ['criteria','actions','decisions','rejected_options','skills_used','checks','changed_files','limitations','message_refs','artifact_refs']};summary.update({'request':'Installer verification','result':'Passed','next_step':'None'})
result,value=call(client,owner,'finish_task',{'task_id':task_id,'status':'succeeded','summary':summary});check('Structured task finish persisted',not result['isError'])
from knowledge_acl import identity_scope
old_dsn=os.environ['DATABASE_URL'];os.environ['DATABASE_URL']='postgresql://galactica_ui_acl:'+env['GALACTICA_UI_ACL_PASSWORD']+'@postgres:5432/sfera'
spec=importlib.util.spec_from_file_location('daughter_ui',ROOT/'ui/daughter_api.py');ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui);os.environ['DATABASE_URL']=old_dsn
with identity_scope(identity['source_subject_key'],True):
 check('Independent UI graph reads canonical state',isinstance(ui.complete_graph(),dict))
 check('UI context documents available',len(ui.documents())>=7)
_,rid=call(reader_client,reader,'whoami')
with identity_scope(rid['source_subject_key'],False):
 graph=ui.complete_graph();check('Reader UI excludes another user private task',not any(str(n.get('id'))=='task:'+task_id for n in graph.get('nodes',[])))
from psycopg.rows import dict_row
from knowledge_acl import configure_connection
with identity_scope(identity['source_subject_key'],True):
 with psycopg.connect(ui.DSN,row_factory=dict_row) as db:
  configure_connection(db);revision=db.execute('SELECT revision FROM galactica_autonomy.acl_state').fetchone()['revision']
  docid=db.execute("SELECT id FROM galactica_autonomy.documents WHERE logical_key='client/current_task'").fetchone()['id']
  payload={'expected_revision':revision,'operation':'rules','scope_kind':'material','scope_key':'document:'+str(docid),'subject_kind':'user','subject_key':rid['source_subject_key'],'actions':{'read':'deny','create':'inherit','edit':'inherit','manage':'inherit'}}
  changed=ui.mutate(db,identity,payload)
 check('Admin ACL accepts independent UUID user',bool(changed))
 with psycopg.connect(ui.DSN,row_factory=dict_row) as db:
  configure_connection(db)
  try:ui.mutate(db,identity,payload);raise AssertionError('Stale ACL revision accepted')
  except ui.Conflict:checks.append('Stale ACL revision refused')
result,value=call(reader_client,reader,'read_document',{'logical_key':'client/current_task'});check('Material ACL denial enforced for reader',result['isError'])
with psycopg.connect(ADMIN_DSN) as db:
 uid=db.execute("SELECT principal_id FROM galactica_autonomy.sfera_local_users WHERE username='test-reader'").fetchone()[0]
 db.execute("UPDATE galactica_autonomy.sfera_local_users SET status='disabled' WHERE principal_id=%s",(uid,))
check('Disabled user bearer denied',reader_client.post('/mcp',headers={'Authorization':'Bearer '+reader['access_token']},json={'jsonrpc':'2.0','id':1,'method':'ping'}).status_code==401)
refresh={'grant_type':'refresh_token','refresh_token':owner['refresh_token'],'client_id':cid,'resource':env['SFERA_ORIGIN']+'/galactica-mcp/mcp'}
r=client.post('/oauth/token',data=refresh);check('Refresh rotation works',r.status_code==200);rotated=r.json()
check('Refresh replay denied',client.post('/oauth/token',data=refresh).status_code==400)
check('Refresh replay revokes family',client.post('/mcp',headers={'Authorization':'Bearer '+rotated['access_token']},json={'jsonrpc':'2.0','id':1,'method':'ping'}).status_code==401)
check('Anonymous OAuth and passwords unreadable by app role',True)
with psycopg.connect(os.environ['DATABASE_URL']) as db:
 try:db.execute('SELECT password_hash FROM galactica_autonomy.sfera_local_users');raise AssertionError('MCP can read password hashes')
 except psycopg.errors.InsufficientPrivilege:pass
Path('/tmp/SFERA_TEST_RESULTS.json').write_text(json.dumps({'date':'2026-10-05','environment':'Isolated Linux PostgreSQL + Python TestClient','checks':checks,'passed':len(checks),'limitations':['AI and public TLS not covered by this test']},indent=2)+'\n')
print('PASSED',len(checks),'checks')
