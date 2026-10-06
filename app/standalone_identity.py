"""Independent Sfera identity; no TREND, maternal account or shared sessions."""
import os,re,secrets,hashlib,hmac
from http.cookies import SimpleCookie
from datetime import datetime,UTC
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
def auth_connect():return psycopg.connect(os.environ['AUTH_DATABASE_URL'],row_factory=dict_row)
def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def password_hash(password):
 if not isinstance(password,str) or len(password)<12 or len(password)>256:raise ValueError('Password must have 12–256 characters')
 salt=secrets.token_hex(16)
 return salt+':'+hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def password_matches(password,stored):
 try:
  salt,value=stored.split(':');actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
  return hmac.compare_digest(actual,value)
 except (ValueError,AttributeError):return False
def login(username,password):
 if not re.fullmatch(r'[a-zA-Z0-9_.@-]{3,80}',username) or not isinstance(password,str) or len(password)>256:raise PermissionError('Invalid credentials')
 with auth_connect() as db:
  # Lock the user: attempts and the temporary lockout survive worker restarts.
  row=db.execute('SELECT * FROM galactica_autonomy.sfera_local_users WHERE username=%s FOR UPDATE',(username,)).fetchone()
  if row is None:
   password_matches(password,'0'*32+':'+'0'*128);raise PermissionError('Invalid credentials')
  valid=row['status']=='active' and (row['locked_until'] is None or row['locked_until']<datetime.now(UTC)) and password_matches(password,row['password_hash'])
  if not valid:
   db.execute("UPDATE galactica_autonomy.sfera_local_users SET failed_attempts=failed_attempts+1,locked_until=CASE WHEN failed_attempts>=4 THEN clock_timestamp()+interval '15 minutes' ELSE locked_until END WHERE principal_id=%s",(row['principal_id'],))
   db.commit();raise PermissionError('Invalid credentials')
  token=secrets.token_urlsafe(32)
  credential=db.execute("INSERT INTO public.auth_credentials(principal_id,token_hash,kind,label,expires_at) VALUES (%s,%s,'browser','Sfera session',clock_timestamp()+interval '3 hours') RETURNING id",(row['principal_id'],digest(token))).fetchone()
  db.execute('UPDATE galactica_autonomy.sfera_local_users SET failed_attempts=0,locked_until=NULL WHERE principal_id=%s',(row['principal_id'],))
  return token
def credential_identity(credential=None,cookie_hash=None):
 with auth_connect() as db:
  row=db.execute('SELECT * FROM galactica_autonomy.client_mcp_identity(%s,%s)',(credential,cookie_hash)).fetchone()
  if row is None:raise PermissionError('Session expired or revoked')
  user=db.execute("SELECT username,role FROM galactica_autonomy.sfera_local_users WHERE principal_id=%s AND status='active'",(row['principal_id'],)).fetchone()
  if not user:raise PermissionError('User disabled')
  return {'username':user['username'],'source_subject_key':str(row['principal_id']),'client_key':os.environ['SFERA_CLIENT_KEY'],'role':user['role'],'acl_admin':user['role']=='owner','principal_id':row['principal_id'],'credential_id':row['credential_id'],'expires_at':row['expires_at'],'session_expires_at':int(row['expires_at'].timestamp()),'allowed':True}
def browser_identity(cookie):
 c=SimpleCookie()
 try:c.load(cookie);token=c['galactica_mcp_session'].value
 except (KeyError,ValueError):raise PermissionError('Login required') from None
 if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):raise PermissionError('Invalid session')
 return credential_identity(cookie_hash=digest(token))
def list_users():
 with auth_connect() as db:rows=db.execute('SELECT principal_id,username,role,status FROM galactica_autonomy.sfera_local_users ORDER BY username').fetchall()
 return [{'source_subject_key':str(x['principal_id']),'user_id':str(x['principal_id']),'display_name':x['username'],'is_active':x['status']=='active','username':x['username'],'role':x['role'],'status':x['status']} for x in rows]
