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
PAPER_METADATA = ('id', 'version', 'content_sha256', 'created_at')


def read(table, params):
    return store.request('/rest/v1/' + table + '?' + urllib.parse.urlencode(params))


def collect(table, columns):
    result = []
    while True:
        order = 'module_id.asc,id.asc' if table == 'agent_prompt_versions' else 'created_at.asc,id.asc' if table == 'research_paper_prompt_versions' else 'module_id.asc'
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
    papers = collect('research_paper_prompt_versions', ','.join(PAPER_METADATA))
    if len(rows) + len(papers) > MAX_VERSIONS: raise store.StoreError('Prompt catalogue exceeds its interactive limit.')
    return dict(version=VERSION, prompts=[project(row, active) for row in rows] + [paper_project(row) for row in papers])


def paper_project(row, content=False):
    result = {key: row[key] for key in PAPER_METADATA}
    result.update(module_id='research-paper-intake', updated_at=row['created_at'], published_at=None, active=False, recorded=True)
    if content:
        # Only the stored parameterized template and configuration are projected.
        # The catalogue never reads intake requests, uses, paper text or owners.
        import json
        result.update(system_prompt=row['instructions'], research_context=json.dumps(dict(input_template=row['input_template'], request_configuration=row['request_configuration']), ensure_ascii=False))
    return result


def detail(version_id):
    version_id = identifier(version_id)
    rows = read('agent_prompt_versions', dict(select=','.join((*METADATA, 'system_prompt', 'research_context')), id='eq.' + version_id, limit=1))
    if not rows:
        papers = read('research_paper_prompt_versions', dict(select=','.join((*PAPER_METADATA, 'instructions', 'input_template', 'request_configuration')), id='eq.' + version_id, limit=1))
        return dict(version=VERSION, prompt=paper_project(papers[0], content=True)) if papers else None
    return dict(version=VERSION, prompt=project(rows[0], active_ids(), content=True))
