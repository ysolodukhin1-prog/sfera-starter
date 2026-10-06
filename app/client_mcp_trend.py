"""Compatibility identity adapter. Business-source tools are not enabled."""
from standalone_identity import credential_identity as current_source_identity
TOOL_DEFINITIONS=[]
def touch_source_activity(principal):current_source_identity(principal.token_id)
def unavailable(*args,**kwargs):raise PermissionError('Business source not configured for this Sfera instance')
read_source=read_database=read_abc=unavailable
