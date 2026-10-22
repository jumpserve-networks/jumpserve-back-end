"""Display titles use retained evidence; no AI, queue writes or evidence edits."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import intake
import paper_titles
import scheduler
import store
from workflow import canonical, digest

STUDY = '11111111-1111-4111-8111-111111111111'
ARTIFACT = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
TITLE = 'Making Congestion Control Algorithms Insensitive to Underlying Propagation Delays'
# Transcribed first-page title/byline layout, not a new scientific measurement.
FRONT = 'Making Congestion Control Algorithms Insensitive\nto Underlying Propagation Delays\nCyrus Illick ✉\nColumbia University, New York, NY, USA\nMichael Roger\nColumbia University, New York, NY, USA\nAbstract\nPaper body.'

def uploaded_study():
    return dict(id=STUDY, title='ProxyDelay_Nines_CameraReady', study_kind='paper',
        paper_upload=dict(filename='ProxyDelay_Nines_CameraReady.pdf', sha256='a' * 64))

def packet():
    return dict(original_sha256='a' * 64, pages=[dict(page=1, text=FRONT)])

def artifact(raw):
    return dict(id=ARTIFACT, study_id=STUDY, sha256=digest(raw), byte_count=len(raw),
        provenance=dict(kind='derived-pdf-text', original_sha256='a' * 64))

class PaperTitles(unittest.TestCase):
    def setUp(self):
        paper_titles.retained_title.cache_clear()
        self.addCleanup(paper_titles.retained_title.cache_clear)

    def test_old_packet_without_metadata_extracts_multiline_title_before_byline(self):
        value = packet(); before = copy.deepcopy(value)
        result = paper_titles.extract_title(value)
        self.assertEqual(result['title'], TITLE)
        self.assertEqual(result['page'], 1); self.assertEqual(result['version'], paper_titles.VERSION)
        self.assertEqual(value, before)

    def test_metadata_is_corroborated_and_boilerplate_or_body_only_metadata_is_rejected(self):
        value = dict(document_metadata=dict(title='A Reliable Network Study'),
            pages=[dict(page=1, text='A Reliable\nNetwork Study\nAn unusual multi-author byline\nAbstract\nBody')])
        result = paper_titles.extract_title(value)
        self.assertEqual(result['title'], 'A Reliable Network Study'); self.assertIn('metadata', result['method'])
        for title in ('Different Paper Title', 'Untitled Document', 'microsoft word'):
            value['document_metadata']['title'] = title
            self.assertIsNone(paper_titles.extract_title(value))
        value['document_metadata']['title'] = 'A Body Only Phrase'
        value['pages'][0]['text'] = 'Unknown title layout\nAbstract\nA Body Only Phrase'
        self.assertIsNone(paper_titles.extract_title(value))

    def test_missing_malformed_scanned_and_ambiguous_front_matter_stay_missing(self):
        for value in (None, [], {}, {'pages': [None]}, {'pages': [dict(page=2, text=FRONT)]},
            {'pages': [dict(page=1, text='')]}, {'pages': [dict(page=1, text='Title without an identifiable byline\nAbstract\nBody')]},
            {'pages': [dict(page=1, text='A Study of Networks\nJane Smith\nJohn Smith\nColumbia University\nAbstract')]}):
            self.assertIsNone(paper_titles.extract_title(value), value)

    def test_selected_original_identity_and_packet_hash_are_checked_without_writes(self):
        study = uploaded_study(); before = copy.deepcopy(study); raw = canonical(packet())
        with patch.object(store, 'rows', return_value=[artifact(raw)]) as rows, \
             patch.object(store, 'artifact_bytes', return_value=raw) as read, \
             patch.object(store, 'rpc') as rpc, patch.object(store, 'artifact') as write:
            self.assertEqual(paper_titles.display_title(study), TITLE)
            self.assertEqual(paper_titles.display_title(study), TITLE)
        query = rows.call_args.args[1]
        self.assertEqual(query['study_id'], 'eq.' + STUDY)
        self.assertEqual(query['provenance->>original_sha256'], 'eq.' + study['paper_upload']['sha256'])
        self.assertLessEqual(query['limit'], 2); rows.assert_called_once()
        read.assert_called_once_with(STUDY, ARTIFACT, maximum=paper_titles.MAX_PACKET_BYTES)
        rpc.assert_not_called(); write.assert_not_called(); self.assertEqual(study, before)

    def test_bad_identity_hash_size_and_unavailable_packets_keep_queue_title_explicitly_missing(self):
        raw = canonical(packet())
        cases = [([], raw), ([dict(artifact(raw), study_id=OTHER)], raw),
            ([dict(artifact(raw), provenance=None)], raw), ([dict(artifact(raw), byte_count=None)], raw),
            ([dict(artifact(raw), byte_count=paper_titles.MAX_PACKET_BYTES + 1)], raw),
            ([artifact(raw)], b'bad hash'), ([artifact(canonical(dict(packet(), original_sha256='b' * 64)))], canonical(dict(packet(), original_sha256='b' * 64))),
            ([artifact(b'[]')], b'[]')]
        for records, body in cases:
            paper_titles.retained_title.cache_clear()
            with patch.object(store, 'rows', return_value=records), patch.object(store, 'artifact_bytes', return_value=body):
                self.assertEqual(paper_titles.display_title(uploaded_study()), 'Paper title unavailable')
        with patch.object(store, 'rows', side_effect=store.StoreError('private storage details')):
            self.assertEqual(paper_titles.display_title(uploaded_study()), 'Paper title unavailable')

    def test_explicit_titles_do_not_read_artifacts_and_negative_cache_expires(self):
        study = dict(uploaded_study(), title='A deliberately assigned paper title')
        with patch.object(store, 'rows') as rows:
            self.assertEqual(paper_titles.display_title(study), study['title']); rows.assert_not_called()
        raw = canonical(packet())
        with patch.object(store, 'rows', side_effect=[[], [artifact(raw)]]) as rows, \
             patch.object(store, 'artifact_bytes', return_value=raw), patch.object(paper_titles.time, 'monotonic', side_effect=[1, 2, 31]):
            self.assertEqual(paper_titles.display_title(uploaded_study()), 'Paper title unavailable')
            self.assertEqual(paper_titles.display_title(uploaded_study()), 'Paper title unavailable')
            self.assertEqual(paper_titles.display_title(uploaded_study()), TITLE)
            self.assertEqual(rows.call_count, 2)

    def test_owner_overview_enriches_only_the_selected_owned_paper_and_keeps_coverage(self):
        study = uploaded_study(); other = dict(study, id=OTHER, title='Another owner paper')
        def rows(kind, params):
            if kind == 'study_owners': return [dict(study_id=STUDY), dict(study_id=OTHER)]
            if kind == 'studies': return [study, other]
            if kind == 'queue_jobs': return []
            self.fail('Unexpected title-related read: ' + kind)
        with patch.object(store, 'rows', side_effect=rows), patch.object(paper_titles, 'display_title', return_value=TITLE) as title, \
             patch('autonomy.overview', return_value={}):
            result = scheduler.overview('owner', STUDY)
            title.assert_called_once_with(study)
            self.assertEqual([s['title'] for s in result['studies']], [TITLE, other['title']])
            self.assertEqual(result['coverage'], dict(studies=2, jobs=0))
            title.reset_mock(); scheduler.overview('owner', ARTIFACT); title.assert_not_called()
            with self.assertRaises(ValueError): scheduler.overview('owner', 'invalid')

    def test_new_extraction_retains_optional_metadata_without_changing_page_text(self):
        page = SimpleNamespace(extract_text=lambda: FRONT)
        for metadata in (SimpleNamespace(title=TITLE), None):
            reader = SimpleNamespace(is_encrypted=False, pages=[page], metadata=metadata)
            with patch.dict(sys.modules, pypdf=SimpleNamespace(PdfReader=lambda _: reader, __version__='explicit-test-double')):
                result = intake._extract(b'%PDF-test-double')
            self.assertEqual(result['pages'][0]['text'], FRONT)
            self.assertEqual(result['original_sha256'], digest(b'%PDF-test-double'))
            self.assertEqual(result['document_metadata']['title'], TITLE if metadata else None)
            self.assertEqual(result['review_status'], 'retrieved-unreviewed')

if __name__ == '__main__': unittest.main()
