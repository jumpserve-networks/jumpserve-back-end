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
        self.reviews = []
        self.events = []; self.profiles = []
        self.patchers = [patch.object(store, 'rows', side_effect=self.rows), patch.object(store, 'rpc'),
            patch.object(store, 'artifact_bytes'), patch.object(store, 'artifact'), patch.object(api, 'authenticate')]
        self.mocks = [p.start() for p in self.patchers]
        for p in self.patchers: self.addCleanup(p.stop)
    def rows(self, kind, params=None, public=False):
        self.calls.append((kind, copy.deepcopy(params), public))
        self.assertIn(kind, ('studies', 'autonomy_workflows', 'paper_reviews', 'audit_events', 'profiles'))
        if kind == 'profiles':
            ids = params['id'][4:-1].split(',')
            rows = [r for r in self.profiles if r['id'] in ids]
        elif kind in ('autonomy_workflows', 'paper_reviews', 'audit_events'):
            ids = params['study_id'][4:-1].split(',')
            values = {'autonomy_workflows':self.workflow, 'paper_reviews':self.reviews, 'audit_events':self.events}[kind]
            rows = [r for r in values if r['study_id'] in ids]
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

    def test_saved_tcp88_review_title_is_shared_by_catalogue_and_title_endpoint_without_artifact_reads(self):
        row = self.studies[0]
        row.update(title='TCP88.pdf', paper_upload=dict(filename='TCP88.pdf', sha256='a'*64))
        self.reviews = [dict(study_id=row['id'], status='confirmed', title='Congestion Avoidance and Control',
            confirmation=SECRET, seeds=SECRET, metadata=SECRET, owner_id=SECRET)]
        with patch.object(catalog.paper_titles, 'display_title', side_effect=AssertionError('Known title must not re-read PDF text')):
            page = api.handler(self.event())
            title = api.handler(self.event('/research/papers/'+row['id']+'/title'))
        self.assertEqual(json.loads(page['body'])['papers'][0]['title'], 'Congestion Avoidance and Control')
        self.assertEqual(json.loads(title['body'])['title'], 'Congestion Avoidance and Control')
        self.assertNotIn(SECRET, page['body']); self.assertNotIn(SECRET, title['body'])
        for kind, params, public in self.calls:
            if kind == 'paper_reviews':
                self.assertEqual(params['select'], 'study_id,status,title,authors:metadata->authors'); self.assertFalse(public)
        for mock in self.mocks[1:]: mock.assert_not_called()

    def test_titles_are_matched_by_assessment_and_review_rows_follow_server_page_caps(self):
        selected = self.studies[:5]
        self.reviews = [dict(study_id=row['id'], status='ready', title='Prepared paper title '+str(index)) for index,row in enumerate(selected)]
        result = catalog.papers()
        for index, paper in enumerate(result['papers'][:5]): self.assertEqual(paper['title'], 'Prepared paper title '+str(index))
        self.assertTrue(any(kind == 'paper_reviews' and params.get('offset', 0) > 0 for kind,params,_ in self.calls))
        self.reviews[0]['study_id'] = str(uuid.uuid4())
        # Force an invalid response despite the requested identity predicate.
        original = self.rows
        def foreign(kind, params, public=False):
            return [self.reviews[0]] if kind == 'paper_reviews' and not params.get('offset') else [] if kind == 'paper_reviews' else original(kind,params,public)
        with patch.object(store, 'rows', side_effect=foreign): self.assertEqual(api.handler(self.event())['statusCode'], 503)

    def test_missing_or_placeholder_review_titles_keep_the_conservative_fallback(self):
        row = self.studies[0]; row.update(title='TCP88.pdf', paper_upload=dict(filename='TCP88.pdf', sha256='a'*64))
        for candidate in (None, '', 'Uploaded paper', 'TCP88.pdf'):
            self.reviews = [dict(study_id=row['id'], status='preparing', title=candidate)]
            self.assertIsNone(catalog.papers()['papers'][0]['title'])
            with patch.object(catalog.paper_titles, 'display_title', return_value='Paper title unavailable'):
                self.assertEqual(catalog.title(row['id'])['title'], 'Paper title unavailable')
        self.reviews = [dict(study_id=row['id'], status='confirmed', title='Congestion Avoidance and Control')]*2
        self.assertEqual(api.handler(self.event())['statusCode'], 503)

    def test_authors_use_the_saved_byline_only_when_requested_and_keep_private_metadata_out(self):
        row = self.studies[0]
        authors = ['Van Jacobson', 'Michael J. Karels']
        self.reviews = [dict(study_id=row['id'], status='confirmed', title='Congestion Avoidance and Control',
            authors=dict(version='paper-authors-v1', names=authors, page=1, method=SECRET), metadata=SECRET)]
        legacy = catalog.papers(); self.assertNotIn('authors', legacy['papers'][0])
        response = api.handler(self.event(query=dict(bibliography='1')))
        self.assertEqual(json.loads(response['body'])['papers'][0]['authors'], authors)
        response = api.handler(self.event('/research/papers/'+row['id']+'/title',query=dict(bibliography='1')))
        self.assertEqual(json.loads(response['body'])['authors'], authors); self.assertNotIn(SECRET, response['body'])
        self.assertNotIn('authors', catalog.title(row['id']))
        self.reviews[0]['authors'] = None
        self.assertIsNone(catalog.papers(include_authors=True)['papers'][0]['authors'])

    def test_initiator_name_comes_from_intake_actor_and_only_the_display_name_is_public(self):
        first='11111111-2222-4222-8222-222222222222';second='33333333-2222-4222-8222-222222222222'
        self.events=[dict(study_id=row['id'],actor_id=first if i%2==0 else second,action='intake',details=SECRET) for i,row in enumerate(self.studies[:5])]
        self.profiles=[dict(id=first,first_name='Zoë',last_name='O’Brien',email=SECRET,timezone=SECRET),dict(id=second,first_name='Other',last_name='Researcher',email=SECRET)]
        legacy=catalog.papers();self.assertNotIn('initiated_by',legacy['papers'][0]);self.assertFalse(any(kind=='profiles' for kind,_,_ in self.calls))
        response=api.handler(self.event(query=dict(initiator='1')));self.assertEqual(response['statusCode'],200)
        data=json.loads(response['body']);self.assertEqual([r['initiated_by'] for r in data['papers'][:5]],['Zoë O’Brien','Other Researcher','Zoë O’Brien','Other Researcher','Zoë O’Brien'])
        self.assertIsNone(data['papers'][5]['initiated_by'])
        self.assertNotIn(SECRET,response['body']);self.assertNotIn(first,response['body']);self.assertNotIn(second,response['body'])
        for kind,params,public in self.calls:
            if kind=='profiles':self.assertEqual(params['select'],'id,first_name,last_name');self.assertFalse(public)
            if kind=='audit_events':self.assertEqual(params['action'],'eq.intake');self.assertEqual(params['select'],'study_id,actor_id,action')
        for mock in self.mocks[1:]:mock.assert_not_called()

    def test_missing_blank_or_conflicting_initiators_remain_unknown(self):
        actor='11111111-2222-4222-8222-222222222222';row=self.studies[0]
        self.events=[dict(study_id=row['id'],actor_id=actor,action='intake')]
        self.assertIsNone(catalog.papers(include_initiator=True)['papers'][0]['initiated_by'])
        for first,last in [('', ''),('private@example.org',''),('line\nbreak','Name')]:
            self.profiles=[dict(id=actor,first_name=first,last_name=last)]
            self.assertIsNone(catalog.papers(include_initiator=True)['papers'][0]['initiated_by'])
        self.profiles=[dict(id=actor,first_name='Correct',last_name='Person')]
        self.events.append(dict(study_id=row['id'],actor_id='33333333-2222-4222-8222-222222222222',action='intake'))
        self.assertIsNone(catalog.papers(include_initiator=True)['papers'][0]['initiated_by'])

if __name__ == '__main__': unittest.main()
