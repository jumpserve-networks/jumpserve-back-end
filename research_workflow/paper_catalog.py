"""Public paper intake metadata, independently of scientific publication.

Only successfully created paper studies are listed. Pending/rejected upload
reservations and unpublished ideas stay private. Source extraction is separate
from paging so a list read never downloads PDFs or text packets.
"""
import base64
import datetime as dt
import json
import re
import store
import paper_titles
from workflow import canonical, identifier

VERSION = 'research-paper-catalog-v1'
PAGE_SIZE = 10
WORKFLOW_STATUSES = {'running', 'completed', 'completed-with-blockers', 'deadline', 'budget', 'cancelled'}

def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('Invalid assessment page timestamp.')
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))

def cursor(row):
    return base64.urlsafe_b64encode(canonical(dict(created_at=row['created_at'], id=row['id']))).decode().rstrip('=')

def before_cursor(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value): raise ValueError('Invalid assessment page cursor.')
    try:
        result = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if not isinstance(result, dict) or set(result) != {'created_at', 'id'}: raise ValueError()
        timestamp(result['created_at']); result['id'] = identifier(result['id'])
        return result
    except (ValueError, TypeError, KeyError): raise ValueError('Invalid assessment page cursor.') from None

def collect(kind, params, maximum, public=False):
    # Exhaust PostgREST server-side page caps, rather than treating a short page
    # as proof there are no further records. Every call remains bounded.
    rows = []
    while len(rows) < maximum:
        page = store.rows(kind, dict(params, offset=len(rows), limit=maximum-len(rows)), public=public)
        if not page: break
        if not isinstance(page, list) or len(page) > maximum-len(rows): raise store.StoreError('Paper catalogue coverage is invalid.')
        rows.extend(page)
    return rows

def papers(before=None):
    params = dict(study_kind='eq.paper', select='id,title,study_kind,paper_upload,created_at', order='created_at.desc,id.desc')
    boundary = before_cursor(before) if before is not None else None
    if boundary:
        params['or'] = '(created_at.lt.' + boundary['created_at'] + ',and(created_at.eq.' + boundary['created_at'] + ',id.lt.' + boundary['id'] + '))'
    rows = collect('studies', params, PAGE_SIZE+1)
    keys = []
    for row in rows:
        if row.get('study_kind') != 'paper' or not isinstance(row.get('title'), str): raise store.StoreError('Paper catalogue identity is invalid.')
        keys.append((timestamp(row['created_at']), identifier(row['id'])))
    if len(set(k[1] for k in keys)) != len(keys) or any(a <= b for a, b in zip(keys, keys[1:])):
        raise store.StoreError('Paper catalogue ordering/coverage is invalid.')
    if boundary and any(key >= (timestamp(boundary['created_at']), boundary['id']) for key in keys):
        raise store.StoreError('Paper catalogue page exceeds its boundary.')
    selected = rows[:PAGE_SIZE]; ids = {row['id'] for row in selected}
    filters = {'study_id': 'in.(' + ','.join(sorted(ids)) + ')'}
    workflows = collect('autonomy_workflows', dict(filters, select='study_id,status', order='study_id.asc'), PAGE_SIZE+1) if ids else []
    states = {}
    for row in workflows:
        if row.get('study_id') not in ids or row['study_id'] in states or row.get('status') not in WORKFLOW_STATUSES:
            raise store.StoreError('Paper workflow coverage is invalid.')
        states[row['study_id']] = row['status']
    # Anonymous RLS membership reflects the CURRENT published snapshot, including
    # withdrawals. Service-only catalogue access never makes drafts public data.
    published = collect('studies', dict(id='in.(' + ','.join(sorted(ids)) + ')', select='id', order='id.asc'), PAGE_SIZE+1, public=True) if ids else []
    public_ids = {row['id'] for row in published}
    if len(public_ids) != len(published) or not public_ids.issubset(ids): raise store.StoreError('Paper publication coverage is invalid.')
    results = [dict(id=row['id'], title=None if paper_titles.filename_title(row) else row['title'],
        created_at=row['created_at'], workflow_status=states.get(row['id']), published=row['id'] in public_ids) for row in selected]
    result = dict(version=VERSION, access='public-read-only', papers=results, coverage=dict(papers=len(results)),
        next_cursor=cursor(selected[-1]) if len(rows)>PAGE_SIZE else None)
    if len(canonical(result)) > 200_000: raise store.StoreError('Paper catalogue exceeds its response budget.')
    return result

def title(study_id):
    study_id = identifier(study_id)
    rows = store.rows('studies', dict(id='eq.'+study_id, select='id,title,study_kind,paper_upload', limit=1))
    if not rows or rows[0].get('study_kind') != 'paper': return None
    if len(rows) != 1 or rows[0]['id'] != study_id: raise store.StoreError('Paper title identity differs.')
    return dict(version=VERSION, access='public-read-only', id=study_id, title=paper_titles.display_title(rows[0]))
