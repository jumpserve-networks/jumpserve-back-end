"""Missing bibliography and damaged local availability state remain unknown."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import paper_titles as titles,store
from openclaw_health import WorkerAvailability
from test_paper_titles import packet,uploaded_study,STUDY


class BibliographyHealthContracts(unittest.TestCase):
    def test_provider_access_rejections_persist_a_bounded_pause_without_forcing_token_rotation(self):
        for http_status in (401,403):
            with self.subTest(http_status=http_status),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);auth=root/'auth';spool=root/'spool';spool.mkdir();clock=[100];token=Mock(return_value='synthetic-token')
                gate=WorkerAvailability(auth,spool,token,clock=lambda:clock[0],minimum_free_bytes=0);receipt=dict(status='failed',http_status=http_status,provider_calls=1)
                gate.observe(receipt);state=json.loads((auth/'worker-availability.json').read_bytes());self.assertEqual(state['not_before'],400);self.assertEqual(state['reason'],'provider-access');self.assertIsNone(state['force_refresh']);self.assertFalse(state['quota'])
                self.assertEqual(gate.check(),dict(status='unavailable',reason='provider-access',retry_after_seconds=300));token.assert_called_once_with(force_refresh=False)
                clock[0]=401;restarted=WorkerAvailability(auth,spool,token,clock=lambda:clock[0],minimum_free_bytes=0)
                self.assertEqual(restarted.check()['status'],'ready');self.assertEqual(receipt,dict(status='failed',http_status=http_status,provider_calls=1))

    def test_control_characters_and_incomplete_author_envelopes_cannot_be_displayed(self):
        self.assertEqual(titles.clean('Original\x00Title'),'')
        for names in [None,[],['Original Name']*101]:self.assertIsNone(titles.author_names(names))
        for owner in ['unknown',dict(version='unsupported',names=['Original Name'])]:self.assertIsNone(titles.review_authors(dict(owner_authors=owner)))
        for sha in [None,'invalid']:
            study=uploaded_study();study['paper_upload']['sha256']=sha
            with patch.object(store,'rows') as rows:
                self.assertIsNone(titles.display_authors(study));self.assertEqual(titles.display_title(study),'Paper title unavailable');rows.assert_not_called()

    def test_ambiguous_long_or_unlocated_title_blocks_do_not_invent_an_author(self):
        value=packet();value['pages'][0]['text']='X\nJane Doe ✉\nOriginal University\nAbstract\nBody.'
        self.assertIsNone(titles.extract_title(value))
        value=packet();title='Original Networking Study On Network Latency';value['document_metadata']=dict(title=title);value['pages'][0]['text']='\n'.join(title.split())+'\nJane Doe\nOriginal University\nAbstract\nBody.'
        self.assertEqual(titles.extract_title(value)['title'],title);self.assertIsNone(titles.extract_authors(value))
        value['pages'][0]['text']='Retained publisher banner\n'+title+'\nJane Doe\nOriginal University\nAbstract\nBody.'
        self.assertEqual(titles.extract_authors(value)['names'],['Jane Doe'])

    def test_retained_title_rejects_a_nonrecord_without_reading_original_bytes(self):
        titles.retained_title.cache_clear();self.addCleanup(titles.retained_title.cache_clear)
        with patch.object(store,'rows',return_value=[None]),patch.object(store,'artifact_bytes') as read:
            self.assertIsNone(titles.retained_title(STUDY,'a'*64,1));read.assert_not_called()

    def test_private_availability_file_refuses_links_and_malformed_state_and_ignores_success_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);auth=root/'auth';auth.mkdir();spool=root/'spool';spool.mkdir();gate=WorkerAvailability(auth,spool,Mock(return_value='synthetic-token'))
            path=auth/'worker-availability.json';original=root/'original-state';original.write_text('{}');path.symlink_to(original)
            with self.assertRaises(OSError):gate._read()
            path.unlink();path.write_text(json.dumps(dict(version=1,quota=False,quota_event=-1,force_refresh=None,not_before=0,reason='ready')))
            with self.assertRaises(OSError):gate._read()
            path.unlink()
            with patch.object(gate,'_edit') as edit:gate.observe(dict(status='complete',http_status=200))
            edit.assert_not_called();self.assertEqual(original.read_text(),'{}')


if __name__=='__main__':unittest.main()
