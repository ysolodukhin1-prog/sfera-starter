"""OAuth authorization for CLIENT using GALACTICA's own users and memberships."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from standalone_identity import auth_connect as connect
from client_mcp_core import Principal, PROJECT_ID


SCOPES = frozenset({"sfera:read", "sfera:task", "sfera:write"})


class AuthDenied(PermissionError):
    pass


class AuthUnavailable(RuntimeError):
    pass


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def challenge_for(verifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        raise AuthDenied("Invalid PKCE verifier")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def public_resource() -> str:
    raw = os.environ["CLIENT_MCP_RESOURCE_URL"]
    url = urlsplit(raw)
    if url.scheme != "https" or not url.netloc or not url.path.endswith("/mcp"):
        raise RuntimeError("Invalid MCP resource URL")
    return raw


def redirect_allowed(uri: str) -> bool:
    if not isinstance(uri, str) or len(uri) > 512:
        return False
    value = urlsplit(uri)
    if value.username or value.password or value.fragment or not value.hostname:
        return False
    if value.scheme == "https":
        return True
    return value.scheme == "http" and value.hostname in {"127.0.0.1", "localhost", "::1"}


def register_client(client_name: str, redirect_uris: list[str]) -> dict:
    if (not isinstance(client_name, str) or not 0 < len(client_name) <= 100 or
            not isinstance(redirect_uris, list) or not 0 < len(redirect_uris) <= 10 or
            any(not redirect_allowed(uri) for uri in redirect_uris)):
        raise AuthDenied("Invalid client registration")
    client_id = "sfera-" + secrets.token_urlsafe(24)
    with connect() as db:
        db.execute(
            """INSERT INTO galactica_autonomy.client_mcp_oauth_clients
               (client_id,redirect_uris,client_name) VALUES (%s,%s,%s)""",
            (client_id, redirect_uris, client_name),
        )
    return {"client_id": client_id, "client_name": client_name,
            "redirect_uris": redirect_uris, "token_endpoint_auth_method": "none"}


def _cookie_token(header: str) -> str:
    if not header or len(header) > 4096:
        raise AuthDenied("GALACTICA login required")
    cookie = SimpleCookie()
    try:
        cookie.load(header)
        token = cookie["galactica_mcp_session"].value
    except (KeyError, ValueError):
        raise AuthDenied("GALACTICA login required") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
        raise AuthDenied("Invalid GALACTICA session")
    return token


def _active_identity(db, *, credential_id: UUID | None = None,
                     cookie_hash: str | None = None):
    row = db.execute(
        'SELECT * FROM galactica_autonomy.client_mcp_identity(%s,%s)',
        (credential_id, cookie_hash),
    ).fetchone()
    if row is None:
        raise AuthDenied("GALACTICA CLIENT access denied or session expired")
    from standalone_identity import credential_identity
    source = credential_identity(row['credential_id'])
    row['role'] = source['role']
    row['source_subject'] = source['source_subject_key']
    row['source_username'] = source['username']
    row['acl_admin'] = source['acl_admin']
    return row


def browser_subject(cookie_header: str):
    cookie_hash = sha(_cookie_token(cookie_header))
    with connect() as db:
        row = _active_identity(db, cookie_hash=cookie_hash)
    return row, cookie_hash


def _allowed_scopes(role: str) -> frozenset[str]:
    allowed = {"sfera:read"}
    if role in {"contributor", "manager", "owner"}:
        allowed.add("sfera:task")
    if role in {"manager", "owner"}:
        allowed.add("sfera:write")
    return frozenset(allowed)


def begin_authorization(params: dict, cookie: str) -> tuple[UUID, str]:
    required = {"response_type", "client_id", "redirect_uri", "state",
                "code_challenge", "code_challenge_method"}
    if not required <= set(params) or set(params) - (required | {"resource", "scope"}) or any(
        not isinstance(v, str) for v in params.values()
    ):
        raise AuthDenied("Invalid authorization request")
    if (params["response_type"] != "code" or params["code_challenge_method"] != "S256" or
            params.get("resource", public_resource()) != public_resource() or
            not re.fullmatch(r"[A-Za-z0-9_-]{43}", params["code_challenge"]) or
            not 0 < len(params["state"]) <= 512):
        raise AuthDenied("Invalid authorization request")
    scopes = frozenset(params.get("scope", "sfera:read sfera:task").split())
    if not scopes or not scopes <= SCOPES:
        raise AuthDenied("Unsupported CLIENT scope")
    row, cookie_hash = browser_subject(cookie)
    scopes = scopes & _allowed_scopes(row["role"])
    if not scopes:
        raise AuthDenied("GALACTICA role does not grant requested scope")
    request_id, csrf = uuid4(), secrets.token_urlsafe(32)
    with connect() as db:
        client = db.execute(
            """SELECT redirect_uris FROM galactica_autonomy.client_mcp_oauth_clients
               WHERE client_id=%s""", (params["client_id"],),
        ).fetchone()
        if client is None or params["redirect_uri"] not in client["redirect_uris"]:
            raise AuthDenied("Unregistered callback")
        db.execute(
            """INSERT INTO galactica_autonomy.client_mcp_authorization_requests
               (id,client_id,redirect_uri,state,code_challenge,resource,scopes,
                principal_id,credential_id,browser_cookie_hash,csrf_hash,expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       LEAST(clock_timestamp()+interval '5 minutes',%s))""",
            (request_id, params["client_id"], params["redirect_uri"], params["state"],
             params["code_challenge"], public_resource(), sorted(scopes),
             row["principal_id"], row["credential_id"], cookie_hash, sha(csrf),
             row["expires_at"]),
        )
    return request_id, csrf


def authorization_details(request_id: UUID):
    with connect() as db:
        row=db.execute("""SELECT r.scopes,c.client_name,r.redirect_uri
            FROM galactica_autonomy.client_mcp_authorization_requests r
            JOIN galactica_autonomy.client_mcp_oauth_clients c ON c.client_id=r.client_id
            WHERE r.id=%s AND r.expires_at>clock_timestamp() AND r.consumed_at IS NULL""",(request_id,)).fetchone()
    if not row:
        raise AuthDenied("Authorization request expired")
    return row


def approve_authorization(request_id: UUID, csrf: str, cookie: str) -> str:
    if not csrf or len(csrf) > 100:
        raise AuthDenied("Invalid consent")
    identity, cookie_hash = browser_subject(cookie)
    code = secrets.token_urlsafe(32)
    with connect() as db:
        with db.transaction():
            row = db.execute(
                """SELECT * FROM galactica_autonomy.client_mcp_authorization_requests
                   WHERE id=%s AND consumed_at IS NULL AND expires_at>clock_timestamp()
                   FOR UPDATE""", (request_id,),
            ).fetchone()
            if (row is None or row["principal_id"] != identity["principal_id"] or
                    row["credential_id"] != identity["credential_id"] or
                    not hmac.compare_digest(row["browser_cookie_hash"], cookie_hash) or
                    not hmac.compare_digest(row["csrf_hash"], sha(csrf)) or
                    not set(row["scopes"]) <= _allowed_scopes(identity["role"])):
                raise AuthDenied("Consent expired or invalid")
            db.execute(
                """INSERT INTO galactica_autonomy.client_mcp_authorization_codes
                   (code_hash,client_id,redirect_uri,code_challenge,resource,scopes,
                    principal_id,credential_id,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                           LEAST(clock_timestamp()+interval '60 seconds',%s))""",
                (sha(code), row["client_id"], row["redirect_uri"], row["code_challenge"],
                 row["resource"], row["scopes"], row["principal_id"],
                 row["credential_id"], identity["expires_at"]),
            )
            db.execute(
                "UPDATE galactica_autonomy.client_mcp_authorization_requests "
                "SET consumed_at=clock_timestamp() WHERE id=%s", (request_id,),
            )
    separator = "&" if urlsplit(row["redirect_uri"]).query else "?"
    return row["redirect_uri"] + separator + urlencode({"code": code, "state": row["state"]})


# This file is inserted into client_mcp_auth.py by build_patch.py.
def _issue_pair(db, row, identity, *, family_id=None, scopes=None):
    scopes=list(row['scopes'] if scopes is None else scopes)
    if identity['principal_id']!=row['principal_id'] or not set(scopes)<=_allowed_scopes(identity['role']):
        raise AuthDenied('GALACTICA access changed')
    expires=min(int(identity['expires_at'].timestamp()),int(time.time())+3*60*60)
    if expires<=time.time():raise AuthDenied('Session expired')
    family_id=family_id or uuid4()
    access,refresh=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    common=(row['client_id'],row['principal_id'],row['credential_id'],scopes,row['resource'],expires,family_id)
    db.execute('''INSERT INTO galactica_autonomy.client_mcp_access_tokens
        (token_hash,client_id,principal_id,credential_id,scopes,resource,expires_at,family_id)
        VALUES (%s,%s,%s,%s,%s,%s,to_timestamp(%s),%s)''',(sha(access),*common))
    db.execute('''INSERT INTO galactica_autonomy.client_mcp_refresh_tokens
        (token_hash,client_id,principal_id,credential_id,scopes,resource,expires_at,family_id)
        VALUES (%s,%s,%s,%s,%s,%s,to_timestamp(%s),%s)''',(sha(refresh),*common))
    return {'access_token':access,'refresh_token':refresh,'token_type':'Bearer',
            'expires_in':max(1,expires-int(time.time())),'scope':' '.join(scopes)}


def refresh_access(form):
    required={'grant_type','refresh_token','client_id'}
    if not required<=set(form) or set(form)-(required|{'resource','scope'}) or form['grant_type']!='refresh_token':
        raise AuthDenied('Invalid refresh request')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}',form['refresh_token']):raise AuthDenied('Invalid refresh token')
    replay=False
    with connect() as db:
        with db.transaction():
            row=db.execute('''SELECT * FROM galactica_autonomy.client_mcp_refresh_tokens
                WHERE token_hash=%s FOR UPDATE''',(sha(form['refresh_token']),)).fetchone()
            if (row is None or row['client_id']!=form['client_id'] or
                row['resource']!=form.get('resource',public_resource()) or row['revoked_at'] is not None):
                raise AuthDenied('Invalid refresh token')
            if row['consumed_at'] is not None:
                # Commit the family revocation before returning invalid_grant.
                for table in ('client_mcp_access_tokens','client_mcp_refresh_tokens'):
                    db.execute('UPDATE galactica_autonomy.'+table+
                        ' SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE family_id=%s',(row['family_id'],))
                replay=True
            else:
                if row['expires_at'].timestamp()<=time.time():raise AuthDenied('Idle session expired')
                identity=_active_identity(db,credential_id=row['credential_id'])
                scopes=form.get('scope',' '.join(row['scopes'])).split()
                if not scopes or not set(scopes)<=set(row['scopes']):raise AuthDenied('Invalid scope')
                # Refresh verifies activity deadline but never moves it forward.
                result=_issue_pair(db,row,identity,family_id=row['family_id'],scopes=scopes)
                db.execute('UPDATE galactica_autonomy.client_mcp_refresh_tokens SET consumed_at=clock_timestamp() WHERE token_hash=%s',
                           (sha(form['refresh_token']),))
    if replay:raise AuthDenied('Refresh token replay')
    return result


def redeem_code(form: dict) -> dict:
    if form.get('grant_type')=='refresh_token':return refresh_access(form)
    required={'grant_type','code','redirect_uri','client_id','code_verifier'}
    if not required<=set(form) or set(form)-(required|{'resource','scope'}) or form['grant_type']!='authorization_code':
        raise AuthDenied('Invalid token request')
    challenge=challenge_for(form['code_verifier'])
    with connect() as db:
        with db.transaction():
            row=db.execute('''SELECT * FROM galactica_autonomy.client_mcp_authorization_codes
                WHERE code_hash=%s AND consumed_at IS NULL AND expires_at>clock_timestamp() FOR UPDATE''',
                (sha(form['code']),)).fetchone()
            if (row is None or row['client_id']!=form['client_id'] or row['redirect_uri']!=form['redirect_uri'] or
                row['resource']!=form.get('resource',public_resource()) or not hmac.compare_digest(row['code_challenge'],challenge)):
                raise AuthDenied('Authorization code invalid')
            identity=_active_identity(db,credential_id=row['credential_id'])
            result=_issue_pair(db,row,identity)
            db.execute('UPDATE galactica_autonomy.client_mcp_authorization_codes SET consumed_at=clock_timestamp() WHERE code_hash=%s',
                       (sha(form['code']),))
    return result


def authenticate(authorization: str) -> Principal:
    if not authorization.startswith("Bearer "):
        raise AuthDenied("Bearer token required")
    token = authorization[7:]
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise AuthDenied("Invalid bearer token")
    with connect() as db:
        row = db.execute(
            """SELECT principal_id,credential_id,scopes,expires_at
               FROM galactica_autonomy.client_mcp_access_tokens
               WHERE token_hash=%s AND resource=%s AND revoked_at IS NULL
                 AND expires_at>clock_timestamp()""",
            (sha(token), public_resource()),
        ).fetchone()
        if row is None:
            raise AuthDenied("Token expired or revoked")
        identity = _active_identity(db, credential_id=row["credential_id"])
    if (identity["principal_id"] != row["principal_id"] or
            not set(row["scopes"]) <= _allowed_scopes(identity["role"])):
        raise AuthDenied("GALACTICA access changed")
    return Principal(row["principal_id"], row["credential_id"],
                     frozenset(row["scopes"]), int(row["expires_at"].timestamp()),
                     identity['source_subject'],identity['acl_admin'])
