"""Independent Sfera login, OAuth PKCE, and stateless MCP transport."""
import os,json,time,secrets,hmac,re
from html import escape
from urllib.parse import urlencode,parse_qs,urlsplit
from uuid import UUID
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse,Response,RedirectResponse
from knowledge_acl import install_mcp_connection,identity_scope,current_revision
install_mcp_connection()
from client_mcp_auth import AuthDenied,authenticate,begin_authorization,authorization_details,approve_authorization,redeem_code,register_client,browser_subject
from client_mcp_core import dispatch
from standalone_identity import login,browser_identity,auth_connect,digest
from tool_definitions import TOOL_DEFINITIONS
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
ORIGIN=os.environ['SFERA_ORIGIN']
RESOURCE=ORIGIN+'/galactica-mcp/mcp'
def reply(response):
 response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"})
 return response
def page(title,body):return reply(HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+escape(title)+' · Сфера</title><style>body{font:16px system-ui;background:#f5f7fa;color:#202a38}main{max-width:460px;margin:8vh auto;padding:32px;background:white;border-radius:16px}input,button{box-sizing:border-box;width:100%;padding:13px;margin:8px 0}button{background:#2563eb;color:white;border:0;border-radius:8px}a{color:#2563eb}</style><main><h1>'+escape(title)+'</h1>'+body+'</main></html>'))
def destination(value):
 if not isinstance(value,str) or len(value)>4096 or not value.startswith('/') or value.startswith('//') or '\\' in value or any(ord(c)<32 for c in value):raise AuthDenied('Invalid destination')
 p=urlsplit(value)
 if p.scheme or p.netloc or p.fragment or p.path not in {'/galactica/','/galactica-mcp/oauth/authorize'}:raise AuthDenied('Invalid destination')
 return value
def check_origin(request):
 if request.headers.get('Origin')!=ORIGIN:raise AuthDenied('Invalid origin')
@app.get('/healthz')
def health():return {'status':'ok'}
@app.get('/.well-known/oauth-protected-resource')
def resource():return reply(JSONResponse({'resource':RESOURCE,'authorization_servers':[ORIGIN],'scopes_supported':['sfera:read','sfera:task','sfera:write']}))
@app.get('/.well-known/oauth-authorization-server')
def metadata():return reply(JSONResponse({'issuer':ORIGIN,'authorization_endpoint':ORIGIN+'/galactica-mcp/oauth/authorize','token_endpoint':ORIGIN+'/galactica-mcp/oauth/token','registration_endpoint':ORIGIN+'/galactica-mcp/oauth/register','response_types_supported':['code'],'grant_types_supported':['authorization_code','refresh_token'],'code_challenge_methods_supported':['S256'],'token_endpoint_auth_methods_supported':['none'],'scopes_supported':['sfera:read','sfera:task','sfera:write']}))
@app.get('/login')
def form(request:Request):
 try:target=destination(request.query_params.get('next','/galactica/'))
 except AuthDenied:return Response(status_code=400)
 csrf=secrets.token_urlsafe(32)
 r=page('Вход в Сфера','<form method="post"><input name="username" autocomplete="username" required placeholder="Логин"><input type="password" name="password" autocomplete="current-password" required placeholder="Пароль"><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="next" value="'+escape(target,quote=True)+'"><button>Войти</button></form>')
 r.set_cookie('sfera_login_csrf',csrf,secure=True,httponly=True,samesite='strict',max_age=600,path='/login');return r
@app.post('/login')
async def sign_in(request:Request):
 try:
  check_origin(request);raw=await request.body()
  if len(raw)>8192:raise AuthDenied('Too large')
  fields=parse_qs(raw.decode(),keep_blank_values=True)
  if any(len(v)!=1 for v in fields.values()):raise AuthDenied('Duplicate field')
  csrf=fields.get('csrf',[''])[0]
  if not csrf or not hmac.compare_digest(csrf,request.cookies.get('sfera_login_csrf','')):raise AuthDenied('Invalid CSRF')
  target=destination(fields.get('next',['/galactica/'])[0]);token=login(fields.get('username',[''])[0],fields.get('password',[''])[0])
  r=reply(RedirectResponse(target,status_code=303));r.set_cookie('galactica_mcp_session',token,secure=True,httponly=True,samesite='lax',max_age=10800,path='/');r.delete_cookie('sfera_login_csrf',path='/login');return r
 except (AuthDenied,PermissionError,ValueError):return reply(HTMLResponse('Вход не выполнен. Проверь логин и пароль или повтори позже.',status_code=401))
@app.post('/logout')
def logout(request:Request):
 try:
  check_origin(request);identity=browser_identity(request.headers.get('Cookie',''))
  with auth_connect() as db:db.execute('UPDATE public.auth_credentials SET revoked_at=clock_timestamp() WHERE id=%s',(identity['credential_id'],))
  r=reply(RedirectResponse('/login',status_code=303));r.delete_cookie('galactica_mcp_session',path='/');return r
 except PermissionError:return Response(status_code=403)
@app.post('/oauth/register')
async def register(request:Request):
 try:
  raw=await request.body()
  if len(raw)>8192:raise AuthDenied('Too large')
  data=json.loads(raw)
  if data.get('token_endpoint_auth_method','none')!='none':raise AuthDenied('Unsupported auth method')
  return reply(JSONResponse(register_client(data.get('client_name','Sfera assistant'),data.get('redirect_uris')),status_code=201))
 except (ValueError,AuthDenied,AttributeError,TypeError):return reply(JSONResponse({'error':'invalid_client_metadata'},status_code=400))
@app.get('/oauth/authorize')
def authorize(request:Request):
 try:
  if len(request.query_params.multi_items())!=len(dict(request.query_params)):raise AuthDenied('Duplicate parameter')
  try:browser_subject(request.headers.get('Cookie',''))
  except (AuthDenied,PermissionError):return reply(RedirectResponse('/login?'+urlencode({'next':'/galactica-mcp/oauth/authorize?'+request.url.query}),status_code=303))
  rid,csrf=begin_authorization(dict(request.query_params),request.headers.get('Cookie',''));details=authorization_details(rid)
  return page('Подключить помощника','<p>Клиент: '+escape(str(details.get('client_name','Assistant')))+'</p><p>Разрешения: '+escape(str(details.get('scopes',[])))+'</p><form method="post"><input type="hidden" name="request_id" value="'+str(rid)+'"><input type="hidden" name="csrf" value="'+csrf+'"><button>Разрешить</button></form><a href="/galactica/">Отменить</a>')
 except (AuthDenied,ValueError):return Response(status_code=400)
@app.post('/oauth/authorize')
async def consent(request:Request):
 try:
  check_origin(request);raw=await request.body()
  if len(raw)>8192:raise AuthDenied('Too large')
  data=parse_qs(raw.decode())
  if any(len(v)!=1 for v in data.values()):raise AuthDenied('Duplicate field')
  location=approve_authorization(UUID(data['request_id'][0]),data['csrf'][0],request.headers.get('Cookie',''))
  return reply(RedirectResponse(location,status_code=303))
 except (AuthDenied,PermissionError,ValueError,KeyError):return Response(status_code=403)
@app.post('/oauth/token')
async def token(request:Request):
 try:
  raw=await request.body()
  if len(raw)>8192:raise AuthDenied('Too large')
  fields=parse_qs(raw.decode(),keep_blank_values=True)
  if any(len(v)!=1 for v in fields.values()):raise AuthDenied('Duplicate field')
  return reply(JSONResponse(redeem_code({k:v[0] for k,v in fields.items()})))
 except (AuthDenied,PermissionError,ValueError):return reply(JSONResponse({'error':'invalid_grant'},status_code=400))
@app.post('/mcp')
async def mcp(request:Request):
 if request.headers.get('Origin') not in (None,ORIGIN):return Response(status_code=403)
 try:principal=authenticate(request.headers.get('Authorization',''))
 except (AuthDenied,PermissionError):return reply(JSONResponse({'error':'unauthorized'},status_code=401,headers={'WWW-Authenticate':'Bearer resource_metadata="'+ORIGIN+'/.well-known/oauth-protected-resource"'}))
 try:
  raw=await request.body()
  if len(raw)>1048576:return Response(status_code=413)
  msg=json.loads(raw)
  if not isinstance(msg,dict) or msg.get('jsonrpc')!='2.0':raise ValueError('Invalid RPC')
  identifier=msg.get('id');method=msg.get('method')
  if method=='notifications/initialized':return Response(status_code=202)
  if identifier is None:raise ValueError('Missing id')
  if method=='initialize':result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'sfera','version':'1.0.0'}}
  elif method=='ping':result={}
  elif method=='tools/list':result={'tools':TOOL_DEFINITIONS}
  elif method=='tools/call':
   params=msg.get('params',{})
   if not isinstance(params,dict) or not isinstance(params.get('arguments',{}),dict):raise ValueError('Invalid arguments')
   try:
    with identity_scope(principal.source_subject,principal.acl_admin):
     revision=current_revision();value=dispatch(principal,params.get('name',''),params.get('arguments',{}))
     if current_revision()!=revision:raise PermissionError('Permissions changed; retry')
    authenticate(request.headers.get('Authorization',''))
    result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False,default=str)}],'isError':False}
   except (PermissionError,ValueError,LookupError) as error:result={'content':[{'type':'text','text':str(error)}],'isError':True}
  else:return reply(JSONResponse({'jsonrpc':'2.0','id':identifier,'error':{'code':-32601,'message':'Method not found'}}))
  return reply(JSONResponse({'jsonrpc':'2.0','id':identifier,'result':result}))
 except (ValueError,TypeError,UnicodeError):return reply(JSONResponse({'error':'invalid_request'},status_code=400))
