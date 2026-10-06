"""Canonical PostgreSQL thread archive. No shared knowledge/protocolizer writes.

Only the authenticated Principal sets owner identity. RLS is defence in depth;
every query also binds the owner and project explicitly. No admin API override.
"""
from contextlib import contextmanager
import hashlib
import json
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from autonomy_store import PROJECT_ID, SECRET_PATTERNS, connect

CONSENT_VERSION = 'sfera-private-archive-v1'


def text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} must contain 1-{limit} characters')
    return value


@contextmanager
def owned_connection(principal):
    with connect() as db:
        db.execute("SELECT set_config('galactica.archive_owner', %s, true), "
                   "set_config('galactica.archive_project', %s, true)",
                   (str(principal.user_id), str(PROJECT_ID)))
        yield db


def owned_thread(db, principal, thread_id):
    row = db.execute(
        'SELECT * FROM galactica_autonomy.client_threads '
        'WHERE id=%s AND owner_id=%s AND project_id=%s FOR UPDATE',
        (thread_id, principal.user_id, PROJECT_ID)).fetchone()
    if row is None:
        raise PermissionError('Thread is not available to this user')
    return row


def configure_thread_archive(principal, args):
    enabled = args.get('recording_enabled')
    if type(enabled) is not bool:
        raise ValueError('recording_enabled must be boolean')
    if enabled and args.get('consent_version') != CONSENT_VERSION:
        raise ValueError('Explain private recording and technical administrator access; '
                         f'confirm consent_version={CONSENT_VERSION} before enabling')
    with owned_connection(principal) as db:
        if args.get('thread_id'):
            thread_id = UUID(str(args['thread_id']))
            owned_thread(db, principal, thread_id)
            row = db.execute(
                'UPDATE galactica_autonomy.client_threads SET recording_enabled=%s, '
                'consent_at=CASE WHEN %s THEN now() ELSE consent_at END '
                'WHERE id=%s AND owner_id=%s AND project_id=%s RETURNING *',
                (enabled, enabled, thread_id, principal.user_id, PROJECT_ID)).fetchone()
        else:
            client = args.get('source_client')
            if client not in {'codex', 'claude'}:
                raise ValueError('source_client must be codex or claude')
            source = text(args.get('source_thread_id'), 'source_thread_id', 200)
            row = db.execute(
                'INSERT INTO galactica_autonomy.client_threads '
                '(id,project_id,owner_id,source_client,source_thread_id,recording_enabled,consent_version) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s) '
                'ON CONFLICT (owner_id,project_id,source_client,source_thread_id) DO UPDATE '
                'SET recording_enabled=EXCLUDED.recording_enabled,consent_at=now() RETURNING *',
                (uuid4(), PROJECT_ID, principal.user_id, client, source, enabled,
                 CONSENT_VERSION)).fetchone()
    return {'thread': row, 'visibility': 'author_only',
            'administrator_access': 'Database administrator has technical access',
            'capture': 'Only explicitly supplied visible messages are archived'}


def validate_messages(messages):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 50:
        raise ValueError('Supply 1-50 messages')
    result = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError('Each message must be an object')
        if set(message) - {'source_message_id','source_sequence','role','body',
                           'completeness','redaction_note','task_id'}:
            raise ValueError('Unsupported message fields; never send hidden reasoning or tool data')
        source = text(message.get('source_message_id'), 'source_message_id', 200)
        sequence = message.get('source_sequence')
        if type(sequence) is not int or not 0 <= sequence <= 9223372036854775807:
            raise ValueError('source_sequence must be a nonnegative bigint')
        if message.get('role') not in {'user','assistant'}:
            raise ValueError('Only visible user and assistant messages are accepted')
        body = text(message.get('body'), 'body', 50000)
        if any(pattern.search(body) for pattern in SECRET_PATTERNS):
            raise ValueError('Possible secret: redact locally before sending')
        completeness = message.get('completeness')
        if completeness not in {'exact','partial','redacted'}:
            raise ValueError('Explicit completeness is required')
        note = message.get('redaction_note')
        if completeness == 'redacted' or note is not None:
            note = text(note, 'redaction_note', 500)
            if any(pattern.search(note) for pattern in SECRET_PATTERNS):
                raise ValueError('Redaction note appears to contain a secret')
        result.append({**message, 'source_message_id': source, 'body': body,
                       'completeness': completeness, 'redaction_note': note,
                       'task_id': UUID(str(message['task_id'])) if message.get('task_id') else None})
    if len(json.dumps(messages, ensure_ascii=False)) > 700000:
        raise ValueError('Message batch is too large')
    return result


def append_thread_messages(principal, args):
    thread_id = UUID(str(args.get('thread_id')))
    messages = validate_messages(args.get('messages'))
    acknowledged = []
    with owned_connection(principal) as db:
        thread = owned_thread(db, principal, thread_id)
        if not thread['recording_enabled']:
            raise PermissionError('Recording is paused for this thread')
        for message in messages:
            if message['task_id']:
                task = db.execute(
                    'SELECT id FROM galactica_autonomy.tasks WHERE id=%s AND project_id=%s AND agent_id=%s',
                    (message['task_id'], PROJECT_ID, principal.agent_id)).fetchone()
                if task is None:
                    raise PermissionError('Task is not available to this user')
            message_id = uuid5(NAMESPACE_URL, f'{thread_id}:{message["source_message_id"]}')
            values = (message['source_sequence'], message['role'], message['body'],
                      message['completeness'], message['redaction_note'], message['task_id'])
            old = db.execute(
                'SELECT source_sequence,role,body,completeness,redaction_note,task_id '
                'FROM galactica_autonomy.client_thread_messages '
                'WHERE thread_id=%s AND owner_id=%s AND project_id=%s AND source_message_id=%s',
                (thread_id, principal.user_id, PROJECT_ID, message['source_message_id'])).fetchone()
            if old:
                if tuple(old[key] for key in ('source_sequence','role','body','completeness',
                                             'redaction_note','task_id')) != values:
                    raise ValueError('Source message conflict; existing canonical text is immutable')
            else:
                collision = db.execute(
                    'SELECT id FROM galactica_autonomy.client_thread_messages '
                    'WHERE thread_id=%s AND owner_id=%s AND project_id=%s AND source_sequence=%s',
                    (thread_id, principal.user_id, PROJECT_ID, message['source_sequence'])).fetchone()
                if collision:
                    raise ValueError('Source sequence is already used')
                db.execute(
                    'INSERT INTO galactica_autonomy.client_thread_messages '
                    '(id,thread_id,owner_id,project_id,source_message_id,source_sequence,role,body,'
                    'completeness,redaction_note,task_id,body_sha256) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (message_id,thread_id,principal.user_id,PROJECT_ID,message['source_message_id'],
                     *values,hashlib.sha256(message['body'].encode('utf-8')).hexdigest()))
            acknowledged.append({'source_message_id': message['source_message_id'], 'id': str(message_id)})
    return {'thread_id': str(thread_id), 'acknowledged': acknowledged,
            'visibility': 'author_only', 'batch_atomic': True}


def read_thread_archive(principal, args):
    thread_id = UUID(str(args.get('thread_id')))
    after = args.get('after_sequence', -1)
    limit = args.get('limit', 50)
    if type(after) is not int or not -1 <= after <= 9223372036854775807:
        raise ValueError('Invalid after_sequence')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be 1-100')
    with owned_connection(principal) as db:
        thread = owned_thread(db, principal, thread_id)
        rows = db.execute(
            'SELECT * FROM galactica_autonomy.client_thread_messages '
            'WHERE thread_id=%s AND owner_id=%s AND project_id=%s AND source_sequence>%s '
            'ORDER BY source_sequence LIMIT %s',
            (thread_id,principal.user_id,PROJECT_ID,after,limit + 1)).fetchall()
    page = rows[:limit]
    return {'thread': thread, 'messages': page, 'has_more': len(rows) > limit,
            'next_after_sequence': page[-1]['source_sequence'] if page else after}


METHODS = {'configure_thread_archive': configure_thread_archive,
           'append_thread_messages': append_thread_messages,
           'read_thread_archive': read_thread_archive}

STRING = {'type': 'string'}
TOOL_DEFINITIONS = [
    {'name': 'configure_thread_archive', 'description': 'Enable or pause an author-only canonical PostgreSQL thread archive after informed user agreement. DB administrator has technical access.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['recording_enabled'],
                     'properties': {'thread_id': STRING, 'source_client': {'enum': ['codex','claude']},
                                    'source_thread_id': STRING, 'recording_enabled': {'type': 'boolean'},
                                    'consent_version': {'const': CONSENT_VERSION}}}},
    {'name': 'append_thread_messages', 'description': 'Atomically archive supplied visible messages in own enabled thread. Idempotent retries; immutable source IDs. Never shared knowledge.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['thread_id','messages'],
                     'properties': {'thread_id': STRING, 'messages': {'type': 'array','minItems': 1,'maxItems': 50,
                     'items': {'type': 'object','additionalProperties': False,
                               'required': ['source_message_id','source_sequence','role','body','completeness'],
                               'properties': {'source_message_id': STRING,'source_sequence': {'type': 'integer','minimum': 0},
                                              'role': {'enum': ['user','assistant']},'body': STRING,
                                              'completeness': {'enum': ['exact','partial','redacted']},
                                              'redaction_note': STRING,'task_id': STRING}}}}}},
    {'name': 'read_thread_archive', 'description': 'Read only own canonical dialogue, paginated in source order.',
     'annotations': {'readOnlyHint': True},
     'inputSchema': {'type': 'object','additionalProperties': False,'required': ['thread_id'],
                     'properties': {'thread_id': STRING,'after_sequence': {'type': 'integer','minimum': -1},
                                    'limit': {'type': 'integer','minimum': 1,'maximum': 100}}}},
]
