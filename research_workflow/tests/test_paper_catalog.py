"""All paper submissions are discoverable; only narrow status metadata is public."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import paper_catalog as catalog
import store

SECRET = 'PRIVATE-CANARY-owner@example.org-prompt-storage-token'
def fixture():
    rows = [dict(id=str(uuid.UUID(int=(1 << 120)+i+1, version=4)), title='Submitted paper '+str(i), study_kind='paper',
        paper_upload=None, created_at=(dt.datetime(2026, 10, 22, tzinfo=dt.timezone.utc)-dt.timedelta(seconds=i//2)).isoformat(),
        scope=SECRET, domain=SECRET, owner_id=SECRET, initial_prompt=SECRET) for i in range(126)]
    # Deliberate tied timestamps exercise the UUID tie-breaker.
    rows.sort(key=lambda r: (r['created_at'], r['id']), reverse=True)
    return rows

class PaperCatalog(unittest.TestCase):
    def setUp(self):
        self.studies = fixture(); self.calls = []; self.cap = 3
        self.workflow = [dict(study_id=row['id'], status=status, reason=SECRET, initial_prompt=SECRET)
            for row, status in zip(self.studies, ('running', 'budget', 'cancelled', 'completed', 'completed-with-blockers', 'deadline'))]
        self.published = {self.studies[-1]['id'], self.studies[3]['id']}
        self.patchers = [patch.object(store, 'rows', side_effect=self.rows), patch.object(store, 'rpc'),
            patch.object(store, 'artifact_bytes'), patch.object(store, 'artifact'), patch.object(api, 'authenticate')]
        self.mocks = [p.start() for p in self.patchers]
        for p in self.patchers: self.addCleanup(p.stop)
    def rows(self, kind, params=None, public=False):
        self.calls.append((kind, copy.deepcopy(params), public))
        self.assertIn(kind, ('studies', 'autonomy_workflows'))
        if kind == 'autonomy_workflows':
            ids = params['study_id'][4:-1].split(',')
            rows = [r for r in self.workflow if r['study_id'] in ids]
        else:
            rows = copy.deepcopy(self.studies)
            if public: rows = [dict(id=r['id']) for r in rows if r['id'] in self.published]
            if params.get('study_kind'): rows = [r for r in rows if r.get('study_kind') == 'paper']
            if params.get('id', '').startswith('in.'):
                ids = params['id'][4:-1].split(','); rows = [r for r in rows if r['id'] in ids]
            elif params.get('id', '').startswith('eq.'): rows = [r for r in rows if r['id'] == params['id'][3:]]
            if 'or' in params:
                # Execute the requested strict keyset boundary independently.
                before_time = params['or'].split('created_at.lt.')[1].split(',')[0]
                before_id = params['or'].split('id.lt.')[1].split(')')[0]
                rows = [r for r in rows if (catalog.timestamp(r['created_at']), r['id']) < (catalog.timestamp(before_time), before_id)]
        offset = params.get('offset', 0)
        return copy.deepcopy(rows[offset:offset+min(params['limit'], self.cap)])
    def event(self, path='/research/papers', query=None, method='GET'):
        return dict(rawPath=path, queryStringParameters=query or {}, headers={}, requestContext={'http': {'method': method}})

    def test_all_unpublished_states_and_missing_workflows_are_listed_without_private_data(self):
        # Filename is not bibliographic evidence and never enters the catalogue.
        row = self.studies[0]; row.update(title=SECRET, paper_upload=dict(filename=SECRET+'.pdf', sha256='a'*64))
        response = api.handler(self.event()); self.assertEqual(response['statusCode'], 200)
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        self.assertNotIn(SECRET, response['body'])
        data = json.loads(response['body']); self.assertEqual(data['coverage'], dict(papers=10))
        self.assertIsNone(data['papers'][0]['title']); self.assertFalse(data['papers'][0]['published'])
        self.assertEqual([r['workflow_status'] for r in data['papers'][:6]], [r['status'] for r in self.workflow])
        self.assertIsNone(data['papers'][-1]['workflow_status']); self.assertTrue(data['papers'][3]['published'])
        for mock in self.mocks[1:]: mock.assert_not_called()

    def test_pagination_reaches_more_than_100_submissions_without_ideas_or_duplicates(self):
        self.studies.insert(0, dict(self.studies[0], id=str(uuid.uuid4()), study_kind='idea', title=SECRET))
        seen = []; before = None
        while True:
            page = catalog.papers(before); seen.extend(p['id'] for p in page['papers'])
            before = page['next_cursor']
            if before is None: break
        expected = [r['id'] for r in self.studies if r['study_kind'] == 'paper']
        self.assertEqual(seen, expected); self.assertEqual(len(seen), 126); self.assertEqual(len(set(seen)), 126)
        self.assertTrue(any(params.get('offset', 0)>0 for _, params, _ in self.calls))

    def test_inserting_new_papers_between_pages_does_not_shift_older_page_membership(self):
        first = catalog.papers(); expected = [r['id'] for r in self.studies[10:20]]
        self.studies.insert(0, dict(self.studies[0], id=str(uuid.uuid4()), created_at='2026-10-23T00:00:00+00:00'))
        second = catalog.papers(first['next_cursor'])
        self.assertEqual([r['id'] for r in second['papers']], expected)

    def test_invalid_cursor_rejects_before_any_storage_read_and_empty_is_explicit(self):
        for cursor in ('', 'a', 'malicious,(query)', 'x'*257, catalog.cursor(dict(id='invalid', created_at='2026-10-22T00:00:00Z'))):
            self.calls.clear(); response = api.handler(self.event(query=dict(before=cursor)))
            self.assertEqual(response['statusCode'], 400); self.assertEqual(self.calls, [])
        self.studies = []; self.workflow = []
        self.assertEqual(catalog.papers()['coverage'], dict(papers=0)); self.assertIsNone(catalog.papers()['next_cursor'])

    def test_wrong_kind_duplicate_rows_and_foreign_workflow_fail_without_private_details(self):
        original = self.rows
        for kind_to_change in ('studies', 'autonomy_workflows'):
            def bad_rows(kind, params, public=False):
                rows = original(kind, params, public)
                if rows and kind == kind_to_change and not public:
                    if kind == 'studies': rows[0]['study_kind'] = 'idea'
                    else: rows[0]['study_id'] = str(uuid.uuid4())
                return rows
            with patch.object(store, 'rows', side_effect=bad_rows):
                response = api.handler(self.event()); self.assertEqual(response['statusCode'], 503); self.assertNotIn(SECRET, response['body'])
        self.studies[1] = dict(self.studies[0])
        self.assertEqual(api.handler(self.event())['statusCode'], 503)

    def test_title_is_separate_read_only_and_does_not_disclose_upload_metadata(self):
        row = self.studies[0]
        with patch.object(catalog.paper_titles, 'display_title', return_value='Extracted Paper Title') as title:
            response = api.handler(self.event('/research/papers/'+row['id']+'/title'))
            self.assertEqual(json.loads(response['body']), dict(version=catalog.VERSION, access='public-read-only', id=row['id'], title='Extracted Paper Title'))
            title.assert_called_once()
        row['study_kind'] = 'idea'
        self.assertEqual(api.handler(self.event('/research/papers/'+row['id']+'/title'))['statusCode'], 404)
        self.assertEqual(api.handler(self.event('/research/papers/'+str(uuid.uuid4())+'/title'))['statusCode'], 404)
        with patch.object(api, 'authenticate', side_effect=api.HttpError(401, 'Sign in required.')):
            self.assertEqual(api.handler(self.event(method='POST'))['statusCode'], 401)
        self.mocks[1].assert_not_called(); self.mocks[2].assert_not_called(); self.mocks[3].assert_not_called()

if __name__ == '__main__': unittest.main()
