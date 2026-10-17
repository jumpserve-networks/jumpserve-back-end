"""Exposed development regressions; no new scientific or independent evidence."""
import copy
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import gap_queue
import preparation
import scheduler
import store
from workflow import canonical

ACTOR = str(uuid.uuid4())


class GapQueue(unittest.TestCase):
    def test_capabilities_advertise_only_the_implemented_queue_endpoints(self):
        result=api.dispatch(dict(rawPath='/research/capabilities',requestContext={'http':{'method':'GET'}}))
        self.assertEqual(result['queue']['overview_version'],'research-queue-overview-v1')
        self.assertEqual(result['preparation']['gap_queue_version'],gap_queue.VERSION)
    def setUp(self):
        self.plan, _ = preparation.registered({'paper_url': 'https://pure.mpg.de/pubman/item/item_3670144_1'})
        self.plan = copy.deepcopy(self.plan)
        self.item = self.plan['campaigns'][0]
        self.claim = next(e['record'] for e in self.plan['records'] if e['kind'] == 'claims' and e['record']['id'] == self.item['claim_ids'][0])
        self.source = next(e['record'] for e in self.plan['records'] if e['kind'] == 'sources' and e['record']['id'] == self.claim['source_id'])
        self.study = dict(id=str(uuid.uuid4()), title='Development fixture', paper_url=self.plan['paper_urls'][0], domain='Software verification', scope='No new science', origin_module=None)
        self.gap = dict(id=str(uuid.uuid4()), claim_id=self.claim['id'], status='open', created_at='2026-10-17T00:00:00Z')
        self.snapshot = dict(study=self.study, records=dict(gaps=[self.gap], claims=[self.claim], sources=[self.source]))
        for name, value in [('snapshot', self.snapshot), ('owns', False)]:
            p = patch.object(store, name, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch.object(preparation, 'registered', side_effect=lambda study: (self.plan, canonical(self.plan)))
        p.start(); self.addCleanup(p.stop)

    def body(self, action='enqueue'):
        preview = gap_queue.preview(self.study['id'], ACTOR, self.gap['id'])
        return dict(request_id=str(uuid.uuid4()), gap_id=self.gap['id'], gap_sha256=preview['gap_sha256'], plan_sha256=preview['plan_sha256'], key=self.item['key'], action=action)

    def test_exact_claim_and_source_version_match_required(self):
        self.assertTrue(gap_queue.preview(self.study['id'], ACTOR, self.gap['id'])['choices'])
        self.source = copy.deepcopy(self.source); self.source['sha256'] = 'a' * 64
        self.snapshot['records']['sources'] = [self.source]
        result = gap_queue.preview(self.study['id'], ACTOR, self.gap['id'])
        self.assertEqual(result['choices'], []); self.assertTrue(result['unavailable_reason'])

    def test_unknown_plan_or_inaccessible_study_does_not_fabricate_a_job(self):
        with patch.object(preparation, 'registered', return_value=(None, None)):
            self.assertEqual(gap_queue.preview(self.study['id'], ACTOR, self.gap['id'])['choices'], [])
        with patch.object(store, 'snapshot', return_value=None):
            with self.assertRaises(ValueError): gap_queue.preview(self.study['id'], ACTOR, self.gap['id'])

    def test_stale_resolved_and_superseded_gaps_rejected_before_writes(self):
        body = self.body(); self.gap['status'] = 'blocked'
        with patch.object(store, 'rpc') as rpc:
            with self.assertRaisesRegex(ValueError, 'changed'): gap_queue.enqueue(self.study['id'], ACTOR, body)
            rpc.assert_not_called()
        self.gap['status'] = 'resolved'
        with self.assertRaises(ValueError): self.body()
        self.gap['status'] = 'open'
        self.snapshot['records']['gaps'].append(dict(self.gap, id=str(uuid.uuid4()), created_at='2026-10-18T00:00:00Z'))
        with self.assertRaisesRegex(ValueError, 'newer'): self.body()

    def test_followup_identity_is_stable_owner_scoped_and_originals_unchanged(self):
        before = canonical(self.snapshot); body = self.body('create-followup')
        with patch.object(store, 'rpc') as rpc, patch.object(store, 'artifact', return_value={}), patch.object(store, 'append'):
            first = gap_queue.enqueue(self.study['id'], ACTOR, body)
            second = gap_queue.enqueue(self.study['id'], ACTOR, dict(body, request_id=str(uuid.uuid4())))
            other = gap_queue.enqueue(self.study['id'], str(uuid.uuid4()), body)
        self.assertEqual(first, second); self.assertNotEqual(first['study_id'], other['study_id'])
        self.assertNotEqual(first['study_id'], self.study['id']); self.assertEqual(canonical(self.snapshot), before)
        self.assertTrue(all(call.args[0] == 'research_create_study' for call in rpc.call_args_list))

    def test_only_selected_campaign_and_transitive_prerequisites_are_queued(self):
        self.item['dependencies'] = [dict(key=self.plan['campaigns'][-1]['key'], requirement='reviewed-evidence')]
        self.plan = preparation.parse(canonical(self.plan))
        body = self.body()
        calls = []
        def enqueue(study, actor, request):
            calls.append(request['job_id']); return dict(job=dict(id=request['job_id']))
        with patch.object(store, 'owns', return_value=True), patch.object(store, 'rows', return_value=[{}]), patch.object(preparation, 'enqueue', side_effect=enqueue):
            result = gap_queue.enqueue(self.study['id'], ACTOR, body)
        self.assertEqual(len(calls), 2); self.assertEqual(calls[-1], result['job_id']); self.assertTrue(result['complete'])
        self.assertEqual(result['queued_job_ids'], calls)

    def test_missing_preparation_or_wrong_owner_cannot_enqueue(self):
        body = self.body()
        with patch.object(preparation, 'enqueue') as enqueue:
            with self.assertRaisesRegex(ValueError, 'private follow-up'): gap_queue.enqueue(self.study['id'], ACTOR, body)
            with patch.object(store, 'owns', return_value=True), patch.object(store, 'rows', return_value=[]):
                with self.assertRaisesRegex(ValueError, 'Prepare'): gap_queue.enqueue(self.study['id'], ACTOR, body)
            enqueue.assert_not_called()

    def test_routes_require_authentication_without_trusting_client_identity(self):
        for path in ['/research/queue', '/research/studies/' + self.study['id'] + '/gap-experiments']:
            event = dict(rawPath=path, headers={}, requestContext={'http': {'method': 'GET'}})
            self.assertEqual(api.handler(event)['statusCode'], 401)
        with self.assertRaises(ValueError): gap_queue.enqueue(self.study['id'], ACTOR, dict(self.body(), actor=ACTOR))


class QueueOverview(unittest.TestCase):
    def test_empty_is_explicit_and_other_users_or_ideas_are_not_returned(self):
        with patch.object(store, 'rows', return_value=[]) as rows:
            result = scheduler.overview(ACTOR)
        self.assertEqual(result['coverage'], dict(studies=0, jobs=0))
        self.assertEqual(rows.call_args.args[1]['owner_id'], 'eq.' + ACTOR)
        owned = str(uuid.uuid4())
        with patch.object(store, 'rows', side_effect=[[dict(study_id=owned)], [dict(id=owned, study_kind='idea')]]) as rows:
            self.assertEqual(scheduler.overview(ACTOR)['coverage']['jobs'], 0)
            self.assertEqual(rows.call_count, 2)

    def test_incomplete_or_oversized_coverage_is_not_reported_as_empty(self):
        with patch.object(store, 'rows', return_value=[{}] * 101):
            with self.assertRaises(store.StoreError): scheduler.overview(ACTOR)
        with patch.object(store, 'rows', side_effect=[[dict(study_id=str(uuid.uuid4()))], []]):
            with self.assertRaises(store.StoreError): scheduler.overview(ACTOR)

    def test_blocked_failed_cancelled_and_missing_run_status_are_preserved(self):
        study = str(uuid.uuid4()); campaign = str(uuid.uuid4()); previous = str(uuid.uuid4())
        jobs = [dict(id=previous, study_id=study, campaign_id=campaign, status='failed', execution_mode='automatic', dependencies=[], reason='Original failure', request={'private': 'input'})]
        jobs.append(dict(jobs[0], id=str(uuid.uuid4()), status='queued', run_id=str(uuid.uuid4()), dependencies=[dict(job_id=previous, requirement='complete-run')]))
        with patch.object(store, 'rows', side_effect=[[dict(study_id=study)], [dict(id=study)], jobs, [dict(id=campaign, title='Actual campaign')], []]):
            result = scheduler.overview(ACTOR)
        self.assertEqual(result['jobs'][0]['status'], 'failed'); self.assertEqual(result['jobs'][1]['readiness'], 'blocked')
        self.assertIsNone(result['jobs'][1]['run_status']); self.assertNotIn('request', result['jobs'][0])


if __name__ == '__main__': unittest.main()
