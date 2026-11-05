"""Public paper intake metadata, independently of scientific publication.

Only successfully created paper studies are listed. Pending/rejected upload
reservations and unpublished ideas stay private. Source extraction is separate
from paging so a list read never downloads PDFs or text packets.
"""
import base64
import datetime as dt
import json
import re
import unicodedata
import urllib.error
from concurrent.futures import ThreadPoolExecutor
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

def prepared_bibliography(studies):
    """Reuse saved bibliographic fields; expose no private review payload."""
    ids = {row['id'] for row in studies}
    if not ids: return {}
    reviews = collect('paper_reviews', dict(study_id='in.(' + ','.join(sorted(ids)) + ')',
        select='study_id,status,title,authors:metadata->authors,owner_authors:metadata->owner_authors', order='study_id.asc'), len(ids)+1)
    titles, seen = {}, set()
    for review in reviews:
        study_id = review.get('study_id')
        if study_id not in ids or study_id in seen or review.get('status') not in ('preparing', 'ready', 'failed', 'confirmed'):
            raise store.StoreError('Paper title review coverage is invalid.')
        seen.add(study_id)
        candidate = paper_titles.clean(review.get('title'))
        titles[study_id] = dict(title=candidate if paper_titles.valid(candidate) else None,
            authors=paper_titles.review_authors(review),authors_confirmed=review.get('owner_authors') is not None)
    return titles

def display_name(value):
    if not isinstance(value, str): return None
    name = value.strip()
    if not name or len(name) > 201 or any(unicodedata.category(c) == 'Cc' for c in name) or re.search(r'@|https?://|[<>]', name, re.I): return None
    return name

def google_display_name(actor):
    """Read one recorded actor's Google name on the server; emit no auth data."""
    try: user = store.request('/auth/v1/admin/users/' + identifier(actor), timeout=3)
    except (store.StoreError, urllib.error.URLError, ValueError, TypeError): return None
    if not isinstance(user, dict) or user.get('id') != actor or user.get('is_anonymous'): return None
    identities = user.get('identities')
    if not isinstance(identities, list): return None
    google = [identity.get('identity_data') for identity in identities if isinstance(identity, dict) and identity.get('provider') == 'google' and isinstance(identity.get('identity_data'), dict)]
    names = {name for identity in google if (name := display_name(identity.get('full_name')) or display_name(identity.get('name')))}
    if len(names) == 1: return next(iter(names))
    # Do not guess between conflicting linked accounts or use an email address.
    return None

def initiator_names(studies):
    """Publish the recorded intake actor's profile name, then Google name."""
    ids = {row['id'] for row in studies}
    if not ids: return {}
    events = collect('audit_events', dict(study_id='in.(' + ','.join(sorted(ids)) + ')',
        action='eq.intake', select='study_id,actor_id,action', order='study_id.asc,id.asc'), PAGE_SIZE*10+1)
    if len(events) > PAGE_SIZE*10: raise store.StoreError('Assessment initiator coverage exceeds the interactive bound.')
    actors = {}
    for event in events:
        if event.get('study_id') not in ids or event.get('action') != 'intake': raise store.StoreError('Assessment initiator coverage is invalid.')
        try: actor = identifier(event.get('actor_id'))
        except (ValueError, TypeError): raise store.StoreError('Assessment initiator identity is invalid.') from None
        actors.setdefault(event['study_id'], set()).add(actor)
    # Conflicting historical intake actors are ambiguous, not a reason to pick
    # the current owner or whoever happens to be viewing the public page.
    actor_ids = {next(iter(value)) for value in actors.values() if len(value) == 1}
    if not actor_ids: return {}
    profiles = collect('profiles', dict(id='in.(' + ','.join(sorted(actor_ids)) + ')',
        select='id,first_name,last_name', order='id.asc'), len(actor_ids)+1)
    names, seen = {}, set()
    for profile in profiles:
        actor = profile.get('id')
        if actor not in actor_ids or actor in seen: raise store.StoreError('Assessment initiator profile coverage is invalid.')
        seen.add(actor)
        parts = [profile.get('first_name'), profile.get('last_name')]
        if any(not isinstance(part, str) or len(part) > 100 or any(unicodedata.category(c) == 'Cc' for c in part) for part in parts): continue
        name = display_name(' '.join(part.strip() for part in parts if part.strip()))
        if name: names[actor] = name
    missing = sorted(actor_ids - names.keys())
    if missing:
        # At most ten distinct page actors; bounded parallel lookups keep a slow
        # optional Google name from serially delaying the catalogue.
        with ThreadPoolExecutor(max_workers=min(4, len(missing))) as pool:
            for actor, name in zip(missing, pool.map(google_display_name, missing)):
                if name: names[actor] = name
    return {study: names.get(next(iter(value))) for study, value in actors.items() if len(value) == 1}

def papers(before=None, include_authors=False, include_initiator=False, owner_id=None, study_id=None):
    params = dict(study_kind='eq.paper', select='id,title,study_kind,paper_upload,created_at', order='created_at.desc,id.desc')
    if study_id is not None: params['id'] = 'eq.' + identifier(study_id)
    if owner_id is not None:
        owner_id = identifier(owner_id)
        # Filter in Postgres before applying the keyset boundary and page size.
        # The ownership relation stays service-only and is never returned.
        params['select'] += ',catalog_owner:research_study_owners!inner(owner_id)'
        params['catalog_owner.owner_id'] = 'eq.' + owner_id
    boundary = before_cursor(before) if before is not None else None
    if boundary:
        params['or'] = '(created_at.lt.' + boundary['created_at'] + ',and(created_at.eq.' + boundary['created_at'] + ',id.lt.' + boundary['id'] + '))'
    rows = collect('studies', params, PAGE_SIZE+1)
    keys = []
    for row in rows:
        if row.get('study_kind') != 'paper' or not isinstance(row.get('title'), str): raise store.StoreError('Paper catalogue identity is invalid.')
        if owner_id is not None:
            owners = row.get('catalog_owner')
            if isinstance(owners, dict): owners = [owners]
            if not isinstance(owners, list) or len(owners) != 1 or not isinstance(owners[0], dict) or owners[0].get('owner_id') != owner_id:
                raise store.StoreError('Paper catalogue ownership coverage is invalid.')
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
    bibliography = prepared_bibliography(selected)
    initiators = initiator_names(selected) if include_initiator else {}
    results = [dict(id=row['id'], title=bibliography.get(row['id'], {}).get('title') or (None if paper_titles.filename_title(row) else row['title']),
        **({'authors':bibliography.get(row['id'], {}).get('authors')} if include_authors else {}),
        **({'initiated_by':initiators.get(row['id'])} if include_initiator else {}),
        created_at=row['created_at'], workflow_status=states.get(row['id']), published=row['id'] in public_ids) for row in selected]
    result = dict(version=VERSION, access='owner-read-only' if owner_id is not None else 'public-read-only', papers=results, coverage=dict(papers=len(results)),
        next_cursor=cursor(selected[-1]) if len(rows)>PAGE_SIZE else None)
    if len(canonical(result)) > 200_000: raise store.StoreError('Paper catalogue exceeds its response budget.')
    return result

def detail(study_id):
    study_id = identifier(study_id)
    page = papers(include_authors=True, include_initiator=True, study_id=study_id)
    if not page['papers']: return None
    reviews = store.rows('paper_reviews', dict(study_id='eq.' + study_id, select='study_id,status', limit=2))
    if len(reviews) > 1 or any(row.get('study_id') != study_id or row.get('status') not in ('preparing','ready','failed','confirmed') for row in reviews):
        raise store.StoreError('Paper preparation status coverage is invalid.')
    return dict(version='research-paper-detail-v1', access='public-read-only', paper=page['papers'][0],
        preparation_status=reviews[0]['status'] if reviews else None)

def title(study_id, include_authors=False):
    study_id = identifier(study_id)
    rows = store.rows('studies', dict(id='eq.'+study_id, select='id,title,study_kind,paper_upload', limit=1))
    if not rows or rows[0].get('study_kind') != 'paper': return None
    if len(rows) != 1 or rows[0]['id'] != study_id: raise store.StoreError('Paper title identity differs.')
    saved = prepared_bibliography(rows).get(study_id, {})
    result = dict(version=VERSION, access='public-read-only', id=study_id, title=saved.get('title') or paper_titles.display_title(rows[0]))
    if include_authors: result['authors'] = saved.get('authors') if saved.get('authors_confirmed') else saved.get('authors') or paper_titles.display_authors(rows[0])
    return result
