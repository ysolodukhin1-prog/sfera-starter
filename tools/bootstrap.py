"""Initialize a dedicated empty database; never migrate a production database."""
import os,json,re,getpass,argparse,secrets
from pathlib import Path
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row
from standalone_identity import password_hash
ROOT=Path(os.getenv('SFERA_PACKAGE_ROOT','/app'));INSTANCE=Path(os.getenv('SFERA_INSTANCE_ROOT','/instance'));config=json.loads((INSTANCE/'config.json').read_text());project=UUID(config['project_id']);account=UUID(config['account_id'])
def connect():return psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row)
def create_user(username,password,role):
 if not re.fullmatch(r'[a-zA-Z0-9_.@-]{3,80}',username):raise ValueError('Invalid username')
 if role not in {'reader','contributor','manager','owner'}:raise ValueError('Invalid role')
 hashed=password_hash(password)
 with connect() as db:
  if db.execute('SELECT 1 FROM galactica_autonomy.sfera_local_users WHERE username=%s',(username,)).fetchone():raise ValueError('User exists')
  user=uuid4();db.execute("INSERT INTO public.access_principals(id,kind,display_name,external_subject,status,is_superuser) VALUES (%s,'user',%s,%s,'active',false)",(user,username,'sfera:user:'+str(user)))
  db.execute("INSERT INTO public.account_memberships(account_id,principal_id,role,status) VALUES (%s,%s,%s,'active')",(account,user,role))
  db.execute('INSERT INTO galactica_autonomy.sfera_local_users(principal_id,username,password_hash,role) VALUES (%s,%s,%s,%s)',(user,username,hashed,role))
  # Every role gets explicit grants; private task ownership is separately enforced.
  actions={'reader':['read'],'contributor':['read','create'],'manager':['read','create','edit'],'owner':['read','create','edit','manage']}[role]
  for action in actions:db.execute("INSERT INTO galactica_autonomy.acl_rules(subject_kind,subject_key,scope_kind,scope_key,action,effect) VALUES ('user',%s,'root','*',%s,'allow')",(str(user),action))
 print('User created; password was not printed.')
def initialize():
 with connect() as db:
  count=db.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname IN ('public','galactica_autonomy')").fetchone()['n']
  if count:raise RuntimeError('Database is not empty. Initialization refused.')
  passwords=json.loads((INSTANCE/'bootstrap.json').read_text())['passwords']
  for role in ['galactica_client_mcp','galactica_ui_acl','galactica_autonomy','sfera_auth','galactica_query','galactica_auth_resolver']:
   query=sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(passwords[role])) if role in passwords else sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role))
   db.execute(query)
  db.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public')
  db.execute(Path(os.getenv('SFERA_SCHEMA_FILE',str(ROOT/'schema.sql'))).read_text(encoding='utf-8'))
  db.execute('SET search_path=public,galactica_autonomy')
  db.execute(Path(os.getenv('SFERA_AUTH_SCHEMA_FILE',str(ROOT/'002_standalone.sql'))).read_text(encoding='utf-8'))
  db.execute("INSERT INTO public.accounts(id,account_key,account_type,physical_contour,display_name) VALUES (%s,'sfera-owner','client',%s,%s)",(account,'sfera:'+config['client'],config['name']))
  db.execute("INSERT INTO public.projects(id,slug,name,account_id,visibility_scope) VALUES (%s,%s,%s,%s,'account')",(project,config['client'],config['name'],account))
  areas=[('work','Работа'),('knowledge','Знания'),('skills','Навыки'),('experts','Эксперты'),('threads','История задач'),('self','Система'),('world','Мир'),('user','Пользователь'),('projects','Проекты')]
  for order,(key,title) in enumerate(areas):
   db.execute('INSERT INTO galactica_autonomy.entity_areas(key,title,description,display_order) VALUES (%s,%s,%s,%s)',(key,title,title,order))
   db.execute('INSERT INTO galactica_autonomy.acl_sections(key,title,canonical) VALUES (%s,%s,true)',(key,title))
  kinds={'concept':'knowledge','procedure':'knowledge','decision':'work','requirement':'work','constraint':'work','lesson':'work','failure':'work','alternative':'work','observation':'work','goal':'work','fact':'world','task_insight':'threads','expert_profile':'experts','skill_candidate':'skills','method':'knowledge','design_system':'knowledge','design_principle':'knowledge','source_map':'world','api_catalog':'knowledge','operational_method':'knowledge','engineering_pattern':'knowledge'}
  for key,area in kinds.items():db.execute('INSERT INTO galactica_autonomy.entity_kinds(key,title,default_area) VALUES (%s,%s,%s)',(key,key,area))
  for key in ['SUPPORTS','CONTRADICTS','SUPERSEDES','REQUIRES','ASSOCIATED_WITH']:db.execute('INSERT INTO galactica_autonomy.entity_relation_types(key,title,description) VALUES (%s,%s,%s)',(key,key,key))
  db.execute('INSERT INTO galactica_autonomy.acl_state(singleton,revision) VALUES (true,1)')
 # Only neutral system instructions are seeded. No historical tasks or messages.
 ns=uuid5(NAMESPACE_URL,config['document_namespace']);knowledge=Path(os.getenv('GALACTICA_KNOWLEDGE_ROOT','/knowledge'))
 for src in (ROOT/'knowledge-seed').rglob('*.md'):
  relative=src.relative_to(ROOT/'knowledge-seed');key=relative.as_posix().removesuffix('.md').lower();docid=uuid5(ns,key)
  body=src.read_text(encoding='utf-8').replace('{{CLIENT_NAME}}',config['name']).replace('{{CLIENT_KEY}}',config['client'])
  content='---\ngalactica_id: '+str(docid)+'\ntitle: '+relative.stem+'\nlevel: '+('project' if relative.parts[0]=='CLIENT' else 'global')+'\nproject: '+config['client']+'\nschema_version: 1\n---\n\n'+body
  target=knowledge/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8',newline='\n')
 from autonomy_store import seed_documents
 seed_documents()
 # Packaged general procedures are available; validation claims are not inherited.
 from autonomy_skills import register
 for f in (ROOT/'skill-seed').glob('*.json'):register(json.loads(f.read_text(encoding='utf-8')))
 print('Empty Sfera initialized. No client data imported.')
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True);sub.add_parser('init');u=sub.add_parser('user');u.add_argument('--username',required=True);u.add_argument('--role',choices=['reader','contributor','manager','owner'],default='reader');u.add_argument('--password-stdin',action='store_true');r=sub.add_parser('disable');r.add_argument('--username',required=True);r=sub.add_parser('reset-password');r.add_argument('--username',required=True);args=p.parse_args()
 if args.command=='init':initialize()
 elif args.command=='user':
  import sys
  password=sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('New password (12+ characters): ')
  create_user(args.username,password,args.role)
 else:
  hashed=password_hash(getpass.getpass('New password: ')) if args.command=='reset-password' else None
  with connect() as db:
   user=db.execute('SELECT principal_id,role FROM galactica_autonomy.sfera_local_users WHERE username=%s FOR UPDATE',(args.username,)).fetchone()
   if not user:raise ValueError('Unknown user')
   if args.command=='disable' and user['role']=='owner':
    if db.execute("SELECT count(*) AS n FROM galactica_autonomy.sfera_local_users WHERE role='owner' AND status='active'").fetchone()['n']<=1:raise ValueError('Cannot disable the last owner')
   if hashed:db.execute('UPDATE galactica_autonomy.sfera_local_users SET password_hash=%s,failed_attempts=0,locked_until=NULL WHERE principal_id=%s',(hashed,user['principal_id']))
   else:db.execute("UPDATE galactica_autonomy.sfera_local_users SET status='disabled' WHERE principal_id=%s",(user['principal_id'],))
   db.execute('UPDATE public.auth_credentials SET revoked_at=clock_timestamp() WHERE principal_id=%s',(user['principal_id'],))
  print('User updated; all existing sessions revoked.')
if __name__=='__main__':main()
