"""Owner notification summaries are bounded, read-only projections of real states."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import store

OWNER = '10000000-0000-4000-8000-000000000001'
STUDY = '20000000-0000-4000-8000-000000000001'
OTHER = '20000000-0000-4000-8000-000000000002'
SECRET = 'PRIVATE-prompt-raw-artifact-owner-canary'


class AssessmentStatus(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.workflow = [dict(study_id=STUDY, status='running', updated_at='2026-10-25T18:00:00Z', initial_prompt=SECRET)]
        self.tasks = [dict(id=f'30000000-0000-4000-8000-{i:012}', study_id=STUDY,
            claim_id=None if i == 0 else f'40000000-0000-4000-8000-{i:012}', status=status, label=label, request=SECRET)
            for i, (status, label) in enumerate((('resolved', None), ('resolved', 'reproduced'), ('resolved', 'discrepant'), ('blocked', 'untested'), ('running', 'inconclusive'), ('queued', 'untested')))]
        self.cap = 3
        self.reader = patch.object(store, 'rows', side_effect=self.rows).start()
        self.addCleanup(patch.stopall)

    def rows(self, kind, params=None, public=False):
        self.calls.append((kind, copy.deepcopy(params), public))
        if kind == 'study_owners': return [dict(study_id=STUDY, owner_id=OWNER)]
        if kind == 'studies': return [dict(id=STUDY, title='Paper', study_kind='paper', scope=SECRET, paper_upload=SECRET, owner_id=SECRET)]
        if kind in ('claim_designs', 'paper_reviews'): return []
        if kind == 'autonomy_workflows': return copy.deepcopy(self.workflow)
        if kind == 'autonomy_tasks':
            cursor = params.get('id', 'gt.')[3:]
            return copy.deepcopy([t for t in self.tasks if t['id'] > cursor][:min(self.cap, params['limit'])])
        self.fail('Unexpected relation ' + kind)

    def event(self, query=None):
        return dict(rawPath='/research/studies', queryStringParameters=query or dict(mine='1', status='1'), requestContext={'http': {'method': 'GET'}})

    def test_exact_counts_exclude_intake_and_paginate_despite_a_small_server_cap(self):
        result = api.dispatch(self.event(), OWNER)
        self.assertEqual(result['assessment_status_version'], 'research-assessment-status-v1')
        counts = result['studies'][0]['assessment_status']['counts']
        self.assertEqual(counts, dict(total=5, finished=2, reproduced=1, discrepant=1, blocked=1, running=1, queued=1, waiting=0))
        self.assertNotIn(SECRET, json.dumps(result))
        for kind, params, public in self.calls:
            self.assertFalse(public)
            if kind == 'study_owners': self.assertEqual(params['owner_id'], 'eq.' + OWNER)
            if kind in ('autonomy_tasks', 'autonomy_workflows'):
                self.assertEqual(params['study_id'], 'in.(' + STUDY + ')')
                self.assertNotIn('prompt', params['select']); self.assertNotIn('context', params['select'])
        self.assertEqual(len([c for c in self.calls if c[0] == 'autonomy_tasks']), 3)

    def test_anonymous_requests_and_ordinary_listings_do_not_fetch_private_progress(self):
        with self.assertRaises(api.HttpError): api.dispatch(self.event())
        self.assertEqual(self.calls, [])
        result = api.dispatch(self.event(dict(mine='1')), OWNER)
        self.assertNotIn('assessment_status', result['studies'][0])
        self.assertFalse(any(c[0].startswith('autonomy_') for c in self.calls))

    def test_empty_claim_inventory_is_recorded_zero_but_a_missing_workflow_is_missing(self):
        self.tasks = []; result = api.assessment_statuses([dict(id=STUDY)])
        self.assertEqual(result[STUDY]['counts']['total'], 0)
        self.workflow = []; self.assertEqual(api.assessment_statuses([dict(id=STUDY)]), {})

    def test_owner_listing_projects_the_saved_paper_byline_without_using_owner_identity(self):
        original = self.rows
        names = ['Van Jacobson', 'Michael J. Karels']
        def rows(kind, params=None, public=False):
            if kind == 'paper_reviews':
                self.assertIn('authors:metadata->authors', params['select'])
                return [dict(study_id=STUDY, status='confirmed', title='Congestion Avoidance and Control', domain='Networking',
                    authors=dict(version='paper-authors-v1', names=names, page=1), metadata=SECRET)]
            return original(kind, params, public)
        with patch.object(store,'rows',side_effect=rows):
            result=api.dispatch(self.event(dict(mine='1')), OWNER)
        self.assertEqual(result['studies'][0]['authors'], names)
        self.assertEqual(result['studies'][0]['title'], 'Congestion Avoidance and Control')

    def test_ideas_are_excluded_and_unmatched_workflows_never_enter_a_notification(self):
        self.assertEqual(api.assessment_statuses([dict(id=STUDY, study_kind='idea')]), {})
        self.workflow[0]['study_id'] = OTHER
        with self.assertRaises(store.StoreError): api.assessment_statuses([dict(id=STUDY)])

    def test_duplicate_claims_and_unknown_states_are_unavailable_rather_than_success(self):
        self.tasks[2]['claim_id'] = self.tasks[1]['claim_id']
        with self.assertRaises(store.StoreError): api.assessment_statuses([dict(id=STUDY)])
        self.tasks[2]['claim_id'] = 'different'; self.tasks[1]['status'] = 'invented-success'
        with self.assertRaises(store.StoreError): api.assessment_statuses([dict(id=STUDY)])


if __name__ == '__main__': unittest.main()
