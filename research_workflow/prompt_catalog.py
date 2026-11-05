"""Read-only saved prompt templates. Callers must authenticate before dispatch.

Explicit projections exclude author accounts, evaluations and conversation data.
Content is loaded one version at a time; the index never downloads prompt bodies.
"""
import urllib.parse
import store
from workflow import identifier

VERSION = 'prompt-catalog-v1'
MAX_VERSIONS = 1000
METADATA = ('id', 'module_id', 'version', 'content_sha256', 'created_at', 'updated_at', 'published_at')


def read(table, params):
    return store.request('/rest/v1/' + table + '?' + urllib.parse.urlencode(params))


def collect(table, columns):
    result = []
    while True:
        order = 'module_id.asc,id.asc' if table == 'agent_prompt_versions' else 'module_id.asc'
        page = read(table, dict(select=columns, order=order, offset=len(result), limit=min(200, MAX_VERSIONS + 1 - len(result))))
        if not isinstance(page, list): raise store.StoreError('Invalid prompt catalogue.')
        result.extend(page)
        if len(result) > MAX_VERSIONS: raise store.StoreError('Prompt catalogue exceeds its interactive limit.')
        if not page: return result


def active_ids():
    return {row['module_id']: row['active_version_id'] for row in collect('agent_prompt_settings', 'module_id,active_version_id')}


def project(row, active, content=False):
    result = {key: row[key] for key in METADATA}
    result['active'] = row['published_at'] is not None and active.get(row['module_id']) == row['id']
    if content:
        result.update(system_prompt=row['system_prompt'], research_context=row['research_context'])
    return result


def index():
    active = active_ids()
    rows = collect('agent_prompt_versions', ','.join(METADATA))
    return dict(version=VERSION, prompts=[project(row, active) for row in rows])


def detail(version_id):
    version_id = identifier(version_id)
    rows = read('agent_prompt_versions', dict(select=','.join((*METADATA, 'system_prompt', 'research_context')), id='eq.' + version_id, limit=1))
    if not rows: return None
    return dict(version=VERSION, prompt=project(rows[0], active_ids(), content=True))
