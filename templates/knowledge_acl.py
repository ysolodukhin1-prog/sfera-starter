"""Request-local verified identity and canonical permission checks."""
from contextvars import ContextVar
from contextlib import contextmanager

identity_context=ContextVar('galactica_acl_identity',default=None)

@contextmanager
def identity_scope(subject,admin=False):
    token=identity_context.set({'subject':str(subject),'admin':admin is True})
    try:yield
    finally:identity_context.reset(token)

def configure_connection(connection):
    identity=identity_context.get() or {}
    connection.execute("SELECT set_config('galactica.acl.subject',%s,true),set_config('galactica.acl.admin',%s,true)",
                       (identity.get('subject',''),'true' if identity.get('admin') else 'false'))
    return connection

def install_mcp_connection():
    import autonomy_store
    original=autonomy_store.connect
    if getattr(original,'acl_wrapped',False):return
    def connect():return configure_connection(original())
    connect.acl_wrapped=True
    autonomy_store.connect=connect

def require_permission(connection,kind,key,area,action):
    identity=identity_context.get() or {}
    row=connection.execute('SELECT galactica_autonomy.acl_can(%s,%s,%s,%s,%s) AS allowed',
                           (identity.get('subject',''),kind,str(key),area,action)).fetchone()
    if row['allowed'] is not True:raise PermissionError('Нет доступа к этому разделу или материалу GALACTICA')


def current_revision():
    from autonomy_store import connect
    with connect() as connection:
        return connection.execute('SELECT revision FROM galactica_autonomy.acl_state').fetchone()['revision']
