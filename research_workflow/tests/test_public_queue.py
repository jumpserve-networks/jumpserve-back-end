"""Public status is a new narrow read contract, not relaxed draft/RLS access."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import autonomy
import paper_titles
import scheduler
import store
from workflow import canonical, digest
original_rows = store.rows

STUDY = '11111111-1111-4111-8111-111111111111'
CLAIM = '22222222-2222-4222-8222-222222222222'
TASK = '33333333-3333-4333-8333-333333333333'
JOB = '44444444-4444-4444-8444-444444444444'
RUN = '55555555-5555-4555-8555-555555555555'
OTHER = '66666666-6666-4666-8666-666666666666'
STAMP = '2026-10-22T00:00:00Z'
SECRET = 'PRIVATE-CANARY-owner@example.org-signed-artifact-token'

def fixture():
    return {
        'studies': [dict(id=STUDY, title='Public paper title', study_kind='paper', paper_upload=SECRET)],
        'autonomy_workflows': [dict(study_id=STUDY, initial_prompt=SECRET, initial_sha256=SECRET,
            max_spend_cents=10000, committed_cents=0, max_attempts=None, status='running', reason=SECRET,
            created_at=STAMP, deadline_at=STAMP, updated_at=STAMP)],
        'autonomy_tasks': [dict(id=TASK, study_id=STUDY, claim_id=CLAIM, stage='planning', status='blocked',
            label='untested', attempts=0, reason=SECRET, required_inputs=[SECRET], context=dict(secret=SECRET),
            evidence_progress=dict(required_checks=2, completed_checks=0, scope=SECRET), created_at=STAMP, updated_at=STAMP)],
        'claims': [dict(id=CLAIM, study_id=STUDY, description='A source-grounded candidate claim', scope=SECRET)],
        'queue_jobs': [dict(id=JOB, study_id=STUDY, status='failed', execution_mode='automatic',
            request=SECRET, reason=SECRET, lease_token=SECRET, exclusive_resources=[SECRET], run_id=RUN,
            created_at=STAMP, updated_at=STAMP)],
        'runs': [dict(id=RUN, study_id=STUDY, status='partial', requested_resources=SECRET)],
    }

class PublicStatus(unittest.TestCase):
    def setUp(self):
        self.data = fixture(); self.calls = []; self.page_cap = 200
        self.patchers = [patch.object(store, 'rows', side_effect=self.rows),
            patch.object(autonomy, 'runtime_health', return_value=dict(runtime='openclaw-production-v1',
                model_id='gpt-6.1-sol', status='ready', authorization=SECRET)),
            patch.object(store, 'rpc'), patch.object(store, 'owns'), patch.object(api, 'authenticate'),
            patch.object(store, 'artifact'), patch.object(scheduler, 'wake_worker')]
        self.mocks = [p.start() for p in self.patchers]
        for p in self.patchers: self.addCleanup(p.stop)

    def rows(self, kind, params):
        self.calls.append((kind, copy.deepcopy(params)))
        self.assertNotIn(kind, ('study_owners', 'autonomy_turns', 'artifacts', 'queue_events'))
        rows = copy.deepcopy(self.data[kind])
        if kind == 'studies':
            self.assertEqual(params['id'], 'eq.' + STUDY)
        else:
            self.assertEqual(params['study_id'], 'eq.' + STUDY)
        if params.get('id', '').startswith('in.'):
            ids = params['id'][4:-1].split(','); rows = [r for r in rows if r['id'] in ids]
        offset = params.get('offset', 0)
        return rows[offset:offset + min(params['limit'], self.page_cap)]

    def event(self, method='GET', path=None, query=None):
        return dict(rawPath=path or f'/research/studies/{STUDY}/queue',
            requestContext={'http': {'method': method}}, headers={}, queryStringParameters=query or {'public': '1'})

    def test_anonymous_shared_read_keeps_findings_zero_missing_and_no_private_bytes(self):
        response = api.handler(self.event())
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        self.assertNotIn(SECRET, response['body'])
        data = json.loads(response['body'])
        self.assertEqual(data['access'], 'public-read-only')
        self.assertEqual(data['autonomy']['tasks'][0]['attempts'], 0)
        self.assertEqual(data['autonomy']['tasks'][0]['label'], 'untested')
        self.assertEqual(data['jobs'][0]['run_status'], 'partial')
        self.assertEqual(data['coverage'], {'jobs': 1})
        for mock in self.mocks[2:]: mock.assert_not_called()
        self.data['runs'] = []
        self.assertIsNone(scheduler.public_snapshot(STUDY)['jobs'][0]['run_status'])

    def test_empty_preparation_and_actual_claim_labels_are_not_invented_results(self):
        self.data['queue_jobs'] = []
        self.data['autonomy_tasks'][0].update(claim_id=None, stage='intake', label=None, status='queued')
        data = scheduler.public_snapshot(STUDY)
        self.assertEqual(data['jobs'], []); self.assertIsNone(data['autonomy']['tasks'][0]['label'])
        for label, status in [('reproduced', 'resolved'), ('discrepant', 'resolved'), ('inconclusive', 'blocked')]:
            self.data['autonomy_tasks'][0].update(claim_id=CLAIM, stage='assessment', label=label, status=status)
            task = scheduler.public_snapshot(STUDY)['autonomy']['tasks'][0]
            self.assertEqual((task['label'], task['status']), (label, status))

    def test_uploaded_paper_name_is_public_but_private_file_metadata_and_artifacts_are_not(self):
        paper_titles.retained_title.cache_clear()
        self.addCleanup(paper_titles.retained_title.cache_clear)
        upload = dict(filename='PRIVATE-CANARY-camera-ready.pdf', sha256='a' * 64)
        self.data['studies'][0].update(title=upload['filename'][:-4], paper_upload=upload)
        raw = canonical(dict(original_sha256=upload['sha256'], pages=[dict(page=1,
            text='A Source Grounded Paper Title\nJane Smith ✉\nColumbia University\nAbstract\nBody')]))
        record = dict(id=OTHER, study_id=STUDY, sha256=digest(raw), byte_count=len(raw),
            provenance=dict(kind='derived-pdf-text', original_sha256=upload['sha256']))
        def rows(kind, params):
            if kind != 'artifacts': return self.rows(kind, params)
            self.assertEqual(params['study_id'], 'eq.' + STUDY)
            self.assertEqual(params['provenance->>original_sha256'], 'eq.' + upload['sha256'])
            return [record]
        with patch.object(store, 'rows', side_effect=rows), patch.object(store, 'artifact_bytes', return_value=raw):
            response = api.handler(self.event())
        self.assertEqual(response['statusCode'], 200)
        result = json.loads(response['body'])
        self.assertEqual(result['study'], dict(id=STUDY, title='A Source Grounded Paper Title'))
        for private in ('PRIVATE-CANARY', 'paper_upload', 'document_metadata', 'provenance', upload['sha256'], digest(raw)):
            self.assertNotIn(private, response['body'])
        self.assertEqual(result['coverage'], dict(jobs=1))
        self.assertEqual(self.data['studies'][0]['title'], upload['filename'][:-4])

    def test_missing_studies_and_unpublished_ideas_are_not_public_paper_queues(self):
        for studies in ([], [dict(id=STUDY, title=SECRET, study_kind='idea')]):
            self.data['studies'] = studies
            result = api.handler(self.event())
            self.assertEqual(result['statusCode'], 404); self.assertNotIn(SECRET, result['body'])

    def test_server_page_caps_are_exhausted_and_overflow_is_never_silently_truncated(self):
        self.page_cap = 1
        self.data['queue_jobs'].append(dict(self.data['queue_jobs'][0], id=OTHER, run_id=None))
        data = scheduler.public_snapshot(STUDY)
        self.assertEqual(data['coverage']['jobs'], 2)
        self.assertTrue(any(kind == 'queue_jobs' and p.get('offset') == 2 for kind, p in self.calls))
        self.page_cap = 200
        self.data['queue_jobs'] = [dict(self.data['queue_jobs'][0], id=str(uuid.uuid4())) for _ in range(1001)]
        with self.assertRaisesRegex(store.StoreError, 'coverage budget'): scheduler.public_snapshot(STUDY)

    def test_wrong_study_missing_claim_and_invalid_progress_fail_without_exposing_details(self):
        for mutate in (lambda: self.data['studies'][0].update(id=OTHER),
                       lambda: self.data['autonomy_tasks'][0].update(study_id=OTHER),
                       lambda: self.data.update(claims=[]),
                       lambda: self.data['autonomy_tasks'][0].update(evidence_progress=dict(required_checks=1, completed_checks=2))):
            self.data = fixture(); mutate()
            response = api.handler(self.event())
            self.assertEqual(response['statusCode'], 503); self.assertNotIn(SECRET, response['body'])

    def test_public_flag_does_not_open_listing_history_drafts_or_writes(self):
        # Reset the authentication mock to the actual verifier for rejected writes.
        with patch.object(api, 'authenticate', side_effect=api.HttpError(401, 'Sign in required.')):
            for path in (f'/research/studies/{STUDY}/queue', f'/research/studies/{STUDY}/queue-actions', f'/research/studies/{STUDY}/autonomy'):
                self.assertEqual(api.handler(self.event('POST', path))['statusCode'], 401)
        for path in ('/research/queue', f'/research/studies/{STUDY}/autonomy'):
            self.assertEqual(api.handler(self.event(path=path))['statusCode'], 401)
        self.assertEqual(api.handler(self.event(query={'public': '0'}))['statusCode'], 401)
        with self.assertRaises(ValueError): original_rows('autonomy_turns', public=True)
        with patch.object(store, 'owns', return_value=False):
            for path in (f'/research/studies/{STUDY}/autonomy', f'/research/studies/{STUDY}/queue'):
                with self.assertRaises(api.HttpError) as denied:
                    api.dispatch(self.event(path=path, query={'task': TASK}), OTHER)
                self.assertEqual(denied.exception.status, 404)

if __name__ == '__main__': unittest.main()
