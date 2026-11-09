"""Private PDF lifecycle and review projections with synthetic DB/storage adapters."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paper_uploads as uploads
import paper_selection
import paper_titles
import store
from test_paper_uploads import ACTOR, ID, RAW, request, row
from workflow import digest


class UploadBoundaries(unittest.TestCase):
    def setUp(self):
        self.value = row(); self.claim = str(uuid.uuid4()); self.other = str(uuid.uuid4())
        self.review = dict(study_id=ID, status='ready', reason='Original extraction retained', seeds=[{'claim_id': self.claim}], metadata={}, confirmation=None, revision=3, title='Retained paper title', domain='Networking', coverage={'source_pages': 1})
        self.workflow = dict(status='running', reason='Original workflow reason', max_spend_cents=None, committed_cents=5, deadline_at='2099-01-01T00:00:00Z')
        self.claims = [dict(id=self.claim, description='Selected extracted finding', claim_type='numerical', location='Page 1', source_id=str(uuid.uuid4())), dict(id=self.other, description='Unselected candidate', claim_type='qualitative', location='Page 2', source_id=str(uuid.uuid4()))]
        self.tasks = []
        self.owns = self.enterContext(patch.object(store, 'owns', return_value=True)); self.rows = self.enterContext(patch.object(store, 'rows', side_effect=self.read))
        self.rpc = self.enterContext(patch.object(store, 'rpc', side_effect=self.write)); self.operations = []
        self.defaults = self.enterContext(patch.object(uploads.autonomy, 'current_defaults', return_value={'budget_policy': {'version': 'synthetic-policy'}}))
        self.health = self.enterContext(patch.object(uploads.autonomy, 'runtime_health', return_value={'status': 'ready'}))
        self.wake = self.enterContext(patch.object(uploads.autonomy, 'wake'))
    def read(self, kind, query):
        self.assertTrue(query.get('id') == 'eq.' + ID or query.get('study_id') == 'eq.' + ID)
        return {'paper_uploads': [self.value], 'paper_reviews': [self.review], 'autonomy_workflows': [self.workflow], 'claims': self.claims, 'autonomy_tasks': self.tasks}[kind]
    def write(self, function, payload):
        self.operations.append((function, copy.deepcopy(payload)))
        if payload.get('p_operation') == 'authors-capability': return {'version': paper_titles.REVIEW_AUTHORS_VERSION}
        return copy.deepcopy(self.value)
    def confirm(self, **changes):
        return dict(action='confirm', request_id=ID, title='Owner title', domain='Networking', claim_ids=[self.claim], revision=3, **changes)

    def test_completed_requests_return_the_existing_review_or_workflow_without_new_admission(self):
        self.value['status'] = 'started'
        with patch.object(store, 'request') as storage, patch.object(uploads.autonomy, 'start') as start:
            result = uploads.submit(ACTOR, request()); self.assertEqual(result['result'], dict(id=ID, review_version=uploads.REVIEW_VERSION, status='ready'))
            self.assertEqual(uploads.submit(ACTOR, dict(action='prepare', request_id=ID)), dict(id=ID, review_version=uploads.REVIEW_VERSION, status='ready'))
        storage.assert_not_called(); start.assert_not_called()
        with patch.object(store, 'rows', side_effect=[[], [self.workflow]]), patch.object(uploads.autonomy, 'start', return_value={'existing': True}) as start:
            self.assertEqual(uploads.completed(ACTOR, self.value), {'existing': True})
        start.assert_called_once_with(ACTOR, dict(request_id=ID, paper=self.value['paper'], autonomous=True))
        with patch.object(store, 'rows', side_effect=[[], []]), patch.object(uploads.autonomy, 'start') as start, self.assertRaisesRegex(ValueError, 'already identifies a private paper workspace'): uploads.completed(ACTOR, self.value)
        start.assert_not_called()

    def test_rejected_uploads_and_invalid_actions_never_retrieve_or_replace_original_bytes(self):
        for reason in ['Original rejected PDF', None]:
            self.value.update(status='rejected', reason=reason)
            for body in [request(), dict(action='prepare', request_id=ID)]:
                with self.subTest(reason=reason, action=body['action']), patch.object(store, 'request') as storage, self.assertRaises(ValueError): uploads.submit(ACTOR, body)
                storage.assert_not_called()
        for body in [dict(action='arbitrary', request_id=ID), dict(action='prepare', request_id=ID, bypass=True), dict(action='review', request_id=ID, bypass=True), dict(action='confirm', request_id=ID)]:
            with self.subTest(body=body), self.assertRaises(ValueError): uploads.submit(ACTOR, body)

    def test_upload_permissions_are_scoped_to_one_reserved_object_and_one_bounded_token(self):
        path = '/object/upload/sign/' + uploads.BUCKET + '/' + ID + '/' + request()['sha256']
        for url in ['https://unrelated.example.test' + path + '?token=' + 'x' * 16, path + '?token=short', path + '?token=' + 'x' * 8193, path + '?token=' + 'x' * 16 + '&token=' + 'y' * 16, path + '-changed?token=' + 'x' * 16, path]:
            with self.subTest(url=url[:100]), patch.object(store, 'request', return_value={'url': url}), self.assertRaises(store.StoreError): uploads.submit(ACTOR, request())
        with patch.object(store, 'request', return_value={'url': path + '?token=' + 'x' * 16}): ticket = uploads.submit(ACTOR, request())
        self.assertEqual(ticket['token'], 'x' * 16); self.assertEqual(ticket['maximum_bytes'], 10_000_000)
        self.assertNotIn('token', repr(self.operations))

    def test_private_followup_copies_only_an_owned_byte_identical_pdf_under_a_new_immutable_identity(self):
        target = str(uuid.uuid4()); plain = dict(title='Paper', paper_url='https://paper.example.test/document.pdf', domain='Networking', scope='Exact source version')
        uploads.create_private_study(ACTOR, target, plain, ID); self.assertEqual(self.operations[-1][0], 'research_create_study')
        paper = self.value['paper']; events = []
        def artifact(study, actor, raw, ident, media, provenance):
            events.append('original-persisted'); self.assertEqual((study, actor, raw, media), (target, ACTOR, RAW, 'application/pdf'))
            self.assertEqual(ident, str(uuid.uuid5(uuid.UUID(target), 'paper-original:' + digest(RAW)))); self.assertEqual(provenance['source_study_id'], ID); return dict(id=ident, sha256=digest(raw), byte_count=len(raw))
        with patch.object(store, 'artifact_bytes', return_value=RAW) as read, patch.object(store, 'artifact', side_effect=artifact): uploads.create_private_study(ACTOR, target, paper, ID)
        read.assert_called_once_with(ID, str(uuid.uuid5(uuid.UUID(ID), 'paper-original:' + digest(RAW))), maximum=uploads.MAX_BYTES)
        payload = self.operations[-1][1]; self.assertEqual(payload['p_operation'], 'copy'); self.assertEqual(payload['p_payload']['source_study_id'], ID); self.assertEqual(payload['p_payload']['paper'], paper); self.assertEqual(events, ['original-persisted'])
        self.owns.return_value = False
        with patch.object(store, 'artifact_bytes') as read, self.assertRaisesRegex(ValueError, 'PDF is private'): uploads.create_private_study(ACTOR, target, paper, ID)
        read.assert_not_called(); self.owns.return_value = True
        for raw in [RAW + b'changed', b'x' * len(RAW)]:
            with self.subTest(raw=raw), patch.object(store, 'artifact_bytes', return_value=raw), patch.object(store, 'artifact') as write, self.assertRaisesRegex(ValueError, 'differs from this paper version'): uploads.create_private_study(ACTOR, target, paper, ID)
            write.assert_not_called()

    def test_confirmation_retains_the_exact_selected_groups_and_does_not_rewrite_unselected_claims(self):
        selection = dict(central_claims=[self.claim], testable_findings=[])
        result = uploads.submit(ACTOR, self.confirm(selection=selection, authors=['Doe, Jane', 'María José']))
        self.assertEqual(result['access'], 'owner-private'); self.assertEqual(self.operations[-1][1]['p_operation'], 'confirm')
        self.assertEqual(self.operations[-1][1]['p_payload'], dict(title='Owner title', domain='Networking', revision=3, claim_ids=[self.claim], authors=['Doe, Jane', 'María José'], selection=selection))
        self.assertEqual([claim['id'] for claim in result['claims']], [self.claim]); self.wake.assert_called_once()
        incomplete = dict(self.confirm(selection=selection), claim_ids=[self.claim, self.other])
        with self.assertRaisesRegex(ValueError, 'Selected groups must match'): uploads.submit(ACTOR, incomplete)
        for change in [dict(title=''), dict(title='x' * 301), dict(domain=None), dict(domain='x' * 5001), dict(revision=True), dict(revision=0), dict(claim_ids=[]), dict(claim_ids='not-an-array'), dict(claim_ids=[self.claim] * 201), dict(extra=True)]:
            body = dict(self.confirm(), **change)
            with self.subTest(change=change), self.assertRaises(ValueError): uploads.submit(ACTOR, body)

    def test_review_and_organize_retain_the_original_extracted_claim_inventory_and_owner_controls(self):
        original = copy.deepcopy(self.claims); self.assertEqual(uploads.submit(ACTOR, dict(action='review', request_id=ID))['revision'], 3)
        self.assertEqual(self.operations, []); self.wake.assert_not_called()
        uploads.submit(ACTOR, dict(action='organize', request_id=ID)); self.assertEqual(self.operations[-1][1]['p_operation'], 'organize'); self.wake.assert_called_once(); self.assertEqual(self.claims, original)
        for action in ['review', 'organize', 'confirm']:
            self.owns.return_value = False
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Private paper review'): uploads.submit(ACTOR, dict(action=action, request_id=ID))
        with self.assertRaisesRegex(ValueError, 'Private paper review'): uploads.review(ACTOR, ID)

    def test_confirmation_without_author_edits_preserves_the_extracted_byline(self):
        uploads.submit(ACTOR, self.confirm())
        self.assertEqual([payload['p_operation'] for _, payload in self.operations], ['confirm'])
        self.assertNotIn('authors', self.operations[0][1]['p_payload']); self.wake.assert_called_once()

    def test_review_status_and_progress_are_recorded_without_treating_missing_or_unready_workers_as_complete(self):
        result = uploads.review(ACTOR, ID); self.assertTrue(result['can_start']); self.assertEqual(result['progress'], 'Waiting for paper preparation.'); self.assertEqual(result['limits']['budget_policy'], {'version': 'synthetic-policy'})
        self.review['status'] = 'preparing'; self.workflow.update(status='failed', reason='Original extraction failure'); result = uploads.review(ACTOR, ID)
        self.assertEqual(result['status'], 'failed'); self.assertEqual(result['reason'], 'Original extraction failure'); self.assertFalse(result['can_start'])
        self.workflow['status'] = 'running'; self.tasks = [{'reason': 'Reading recorded page 3'}]
        for runtime in [{'status': 'ready'}, {'status': 'configured'}, {'status': 'auth-required'}, {}]:
            self.health.return_value = runtime
            with self.subTest(runtime=runtime): result = uploads.review(ACTOR, ID)
            if runtime.get('status') in ['ready', 'configured']: self.assertEqual(result['progress'], 'Reading recorded page 3')
            else: self.assertIn('No claim experiments have started', result['progress']); self.assertIn(runtime.get('status', 'unavailable'), result['progress'])

    def test_ready_review_still_observes_cancellation_budget_deadline_and_original_selection_metadata(self):
        for change in [dict(status='cancelled'), dict(status='budget'), dict(status='deadline'), dict(max_spend_cents=9), dict(deadline_at='2000-01-01T00:00:00Z')]:
            original = copy.deepcopy(self.workflow); self.workflow.update(change)
            with self.subTest(change=change): self.assertFalse(uploads.review(ACTOR, ID)['can_start'])
            self.workflow = original
        self.workflow['max_spend_cents'] = 10; self.assertTrue(uploads.review(ACTOR, ID)['can_start'])
        self.review['metadata'] = dict(selection=dict(status='ready', central_claims=[self.claim], testable_findings=[]), owner_selection={'central_claims': [self.claim], 'testable_findings': []}, owner_authors=dict(version=paper_titles.REVIEW_AUTHORS_VERSION, names=['Owner Author']))
        self.review['confirmation'] = dict(title='Confirmed exact title', claim_ids=[self.claim]); original = copy.deepcopy(self.review)
        self.defaults.return_value = {}; result = uploads.review(ACTOR, ID)
        self.assertEqual(result['confirmation']['selection'], original['metadata']['owner_selection']); self.assertEqual(result['confirmation']['authors'], ['Owner Author']); self.assertEqual(result['organization']['status'], 'ready'); self.assertNotIn('budget_policy', result['limits']); self.assertEqual(self.review, original)

    def test_missing_review_duplicate_uploads_and_excess_claim_inventory_never_claim_complete_coverage(self):
        with patch.object(store, 'rows', return_value=[]), self.assertRaisesRegex(ValueError, 'Private paper review'): uploads.review(ACTOR, ID)
        with patch.object(store, 'rows', return_value=[self.value, self.value]), self.assertRaisesRegex(ValueError, 'Private paper upload'): uploads.owned(ACTOR, ID)
        self.claims = self.claims * 101
        with self.assertRaisesRegex(ValueError, 'bounded claim inventory'): uploads.review(ACTOR, ID)


if __name__ == '__main__': unittest.main()
