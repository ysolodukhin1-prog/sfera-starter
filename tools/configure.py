"""Create a new isolated instance. Refuses to overwrite an existing installation."""
import argparse,json,secrets,re,os,shutil,sys
from pathlib import Path
from uuid import uuid4,uuid5,NAMESPACE_URL
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--client',required=True);p.add_argument('--domain',required=True);p.add_argument('--name',default='Сфера');p.add_argument('--http-port',type=int,default=80);p.add_argument('--https-port',type=int,default=443);args=p.parse_args()
 if not re.fullmatch('[a-z][a-z0-9-]{2,40}',args.client):p.error('Client slug: 3–41 lowercase letters/digits/hyphens')
 if not re.fullmatch(r'[\w .-]{1,80}',args.name):p.error('Display name: 1–80 letters/digits/spaces/dots/hyphens')
 if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}',args.domain):p.error('Use a DNS domain without protocol or port')
 if (ROOT/'instance').exists() or (ROOT/'.env').exists():p.error('Instance already configured. Use a fresh checkout for another client.')
 instance=ROOT/'instance';instance.mkdir(mode=0o700);runtime=instance/'runtime';runtime.mkdir()
 project,account=str(uuid4()),str(uuid4());roles=['galactica_client_mcp','galactica_ui_acl','galactica_autonomy','sfera_auth']
 passwords={r:secrets.token_hex(32) for r in roles};admin=secrets.token_hex(32);neo=secrets.token_hex(32)
 replacements={'@@PROJECT_ID@@':project,'@@ACCOUNT_ID@@':account}
 for source in (ROOT/'templates').rglob('*'):
  if not source.is_file() or source.name in {'client_mcp_server.py','client_mcp_trend.py','trend_data_access.py'}:continue
  target=runtime/source.relative_to(ROOT/'templates');target.parent.mkdir(parents=True,exist_ok=True)
  text=source.read_text(encoding='utf-8')
  for old,new in replacements.items():text=text.replace(old,new)
  if target.suffix in {'.js','.html'}:
   text=text.replace('Сфера · CLIENT',args.name).replace('Сфера CLIENT',args.name).replace('Вход через TREND','Вход в Сфера')
   # Existing login links lead to this independent login service.
   text=text.replace('Вход TREND','Вход Сферы')
  target.write_text(text,encoding='utf-8',newline='\n')
 schema=(ROOT/'schema/001_snapshot.sql.in').read_text(encoding='utf-8')
 for old,new in replacements.items():schema=schema.replace(old,new)
 (runtime/'schema.sql').write_text(schema,encoding='utf-8',newline='\n')
 config={'client':args.client,'name':args.name,'domain':args.domain,'project_id':project,'account_id':account,'document_namespace':'galactica:sfera:vps-documents','created':'2026-10-05'}
 (instance/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 variables={'COMPOSE_PROJECT_NAME':'sfera-'+args.client,'SFERA_DOMAIN':args.domain,'SFERA_ORIGIN':'https://'+args.domain,'SFERA_CLIENT_KEY':args.client,'CLIENT_PROJECT_ID':project,'CLIENT_ACCOUNT_ID':account,'HTTP_PORT':str(args.http_port),'HTTPS_PORT':str(args.https_port),'POSTGRES_PASSWORD':admin,'NEO4J_PASSWORD':neo,'LLM_BASE_URL':'http://ollama:11434/v1','EMBEDDING_MODEL':'nomic-embed-text','PROTOCOL_MODEL':'gemma3:4b','QDRANT_COLLECTION':'sfera_'+project.replace('-',''),'QDRANT_VECTOR_SIZE':'768'}
 variables.update({r.upper()+'_PASSWORD':v for r,v in passwords.items()})
 for filename,content in [('.env',''.join(k+'='+v+'\n' for k,v in variables.items())),('instance/bootstrap.json',json.dumps({'passwords':passwords}))]:
  path=ROOT/filename;path.write_text(content,encoding='utf-8');os.chmod(path,0o600)
 print('Configured isolated instance '+args.client+'. New identifiers and database credentials generated; credentials were not printed.')
if __name__=='__main__':main()
