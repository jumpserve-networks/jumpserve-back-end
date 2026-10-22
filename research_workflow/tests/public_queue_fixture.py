"""Fixed loopback-only HTTP fixture: actual handler/projection, synthetic store.

No credentials, model calls, real database reads or experiment execution.
Used only by the public queue browser verification entry point.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_public_queue import fixture, STUDY, TASK, CLAIM, JOB, STAMP, SECRET
from test_paper_titles import uploaded_study, packet, artifact
from test_paper_catalog import fixture as paper_fixture
import api
import autonomy
import paper_titles
import store
from workflow import PUBLIC_KINDS, canonical

mode = 'normal'
title_packet = canonical(packet())
def data():
    value = fixture()
    value['studies'][0] = dict(uploaded_study(), created_at='2026-10-22T00:00:01+00:00')
    extra = paper_fixture()[:12]
    extra[2] = dict(id=extra[2]['id'], created_at=extra[2]['created_at'], study_kind='paper',
        title='Synthetic published paper assessment', paper_url='https://example.org/paper.pdf',
        domain='Networking', scope='Synthetic software fixture; no scientific validation.', origin_module=None, paper_upload=None)
    value['studies'].extend(extra)
    value['studies'].append(dict(extra[0], id='dddddddd-1111-4111-8111-111111111111', study_kind='idea', title=SECRET))
    for paper, status in zip(extra, ('budget', 'cancelled', 'completed', 'completed-with-blockers', 'deadline')):
        value['autonomy_workflows'].append(dict(value['autonomy_workflows'][0], study_id=paper['id'], status=status))
    value['artifacts'] = [] if mode == 'title-missing' else [artifact(title_packet)]
    for index, label in enumerate(('reproduced', 'discrepant', 'inconclusive'), 7):
        claim = str(index) * 8 + '-1111-4111-8111-111111111111'
        task = str(index) * 8 + '-2222-4222-8222-222222222222'
        value['claims'].append(dict(id=claim, study_id=STUDY, description='Synthetic ' + label + ' candidate'))
        value['autonomy_tasks'].append(dict(value['autonomy_tasks'][0], id=task, claim_id=claim,
            label=label, status='blocked' if label == 'inconclusive' else 'resolved', stage='assessment', attempts=3))
    value['autonomy_tasks'].insert(0, dict(value['autonomy_tasks'][0], id='aaaaaaaa-1111-4111-8111-111111111111',
        claim_id=None, stage='intake', status='queued', label=None))
    value['queue_jobs'].append(dict(value['queue_jobs'][0], id='bbbbbbbb-1111-4111-8111-111111111111',
        run_id=None, status='queued'))
    for kind in PUBLIC_KINDS: value.setdefault(kind, [])
    published = extra[2]['id']
    public_claim = dict(id='cccccccc-1111-4111-8111-111111111111', study_id=published, created_at=STAMP,
        description='Synthetic published claim', location='Software fixture', claim_type='numerical')
    value['claims'].append(public_claim)
    value['publications'].append(dict(id='eeeeeeee-1111-4111-8111-111111111111', study_id=published,
        created_at=STAMP, version=1, status='published', record_ids={kind: [public_claim['id']] if kind == 'claims' else [] for kind in PUBLIC_KINDS}))
    if mode == 'empty':
        value['queue_jobs'] = []
        value['autonomy_tasks'] = value['autonomy_tasks'][:1]
    if mode == 'running': value['queue_jobs'][1]['status'] = 'running'
    if mode == 'catalog-empty': value['studies'] = []
    return value

def rows(kind, params, public=False):
    value = data()[kind]
    if kind == 'studies':
        if public:
            value = [p for p in value if p['id'] == paper_fixture()[2]['id']]
            if params.get('select') == 'id': value = [dict(id=p['id']) for p in value]
        if params.get('study_kind'): value = [p for p in value if p.get('study_kind') == 'paper']
        if params.get('id', '').startswith('eq.'): value = [p for p in value if p['id'] == params['id'][3:]]
        if 'or' in params:
            boundary = params['or']; stamp=boundary.split('created_at.lt.')[1].split(',')[0]; identity=boundary.split('id.lt.')[1].split(')')[0]
            from paper_catalog import timestamp
            value = [p for p in value if (timestamp(p['created_at']),p['id']) < (timestamp(stamp),identity)]
    elif params.get('study_id', '').startswith('eq.'):
        value = [r for r in value if r['study_id'] == params['study_id'][3:]]
    elif params.get('study_id', '').startswith('in.'):
        ids=params['study_id'][4:-1].split(',');value=[r for r in value if r['study_id'] in ids]
    else: raise AssertionError('Fixture requires explicit study scope.')
    if params.get('id', '').startswith('in.'):
        ids = params['id'][4:-1].split(','); value = [r for r in value if r['id'] in ids]
    offset = params.get('offset', 0)
    return copy.deepcopy(value[offset:offset + params.get('limit', 200)])

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == '/fixture/health': return self.reply(200, dict(fixture='public-queue-synthetic-store-v1', study=STUDY))
        if mode == 'unavailable': return self.reply(503, dict(error='Explicit software transport failure.'))
        response = api.handler(dict(rawPath=parsed.path, queryStringParameters={k: v[-1] for k, v in parse_qs(parsed.query).items()},
            headers={k.lower(): v for k, v in self.headers.items()}, requestContext={'http': {'method': 'GET'}}))
        result = json.loads(response['body'])
        if mode == 'invalid' and response['statusCode'] == 200: result['coverage']['jobs'] += 1
        if mode == 'catalog-invalid' and response['statusCode'] == 200 and parsed.path == '/research/papers': result['coverage']['papers'] += 1
        self.reply(response['statusCode'], result)
    def do_POST(self):
        global mode
        if self.path != '/fixture/mode': return self.reply(405, dict(error='Fixture does not accept research writes.'))
        request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if request.get('mode') not in ('normal', 'empty', 'running', 'invalid', 'unavailable', 'title-missing', 'catalog-empty', 'catalog-invalid'): return self.reply(400, dict(error='Fixed fixture states only.'))
        mode = request['mode']; paper_titles.retained_title.cache_clear(); self.reply(200, dict(mode=mode))

if __name__ == '__main__':
    if len(sys.argv) != 1: raise SystemExit('Fixed fixture only; no external address or commands.')
    with patch.object(store, 'rows', side_effect=rows), patch.object(store, 'artifact_bytes', return_value=title_packet), patch.object(autonomy, 'runtime_health', return_value=dict(
        runtime='openclaw-production-v1', model_id='explicit-software-fixture', status='ready')):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        print(json.dumps(dict(fixture='public-queue-synthetic-store-v1', port=server.server_port, study=STUDY)), flush=True)
        server.serve_forever()
