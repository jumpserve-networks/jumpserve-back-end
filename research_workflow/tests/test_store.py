import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store
from workflow import PUBLIC_KINDS, digest

class StorageIntegrity(unittest.TestCase):
    def test_known_original_byte_hash(self):
        self.assertEqual(digest(b'abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    def test_artifact_retrieval_is_verified_without_overwriting(self):
        with patch.object(store,'request',return_value=b'abc') as request,patch.object(store,'rows',return_value=[]):
            row=store.artifact(str(uuid.uuid4()),str(uuid.uuid4()),b'abc',str(uuid.uuid4()),'text/plain',{'transformation':'none'})
            self.assertEqual(row['byte_count'],3);self.assertEqual(request.call_count,1)
        with patch.object(store,'request',return_value=b'abc'),patch.object(store,'rows',return_value=[dict(row,study_id='private',created_at='prior')]):
            replay=store.artifact(row['storage_path'].split('/')[0],'actor',b'abc',row['id'],'text/plain',{'transformation':'none'})
            self.assertEqual(replay['verified_at'],row['verified_at'])
        with patch.object(store,'request',return_value=b'tampered'):
            with self.assertRaises(store.StoreError):store.artifact(str(uuid.uuid4()),str(uuid.uuid4()),b'abc',str(uuid.uuid4()),'text/plain',{})
    def test_private_relations_cannot_be_publicly_read(self):
        with self.assertRaises(ValueError):store.rows('study_owners',public=True)
        # Artifacts have an operator-only allowlist, never a snapshot/export kind.
        self.assertNotIn('artifacts',PUBLIC_KINDS)
    def test_snapshot_checks_manifest_coverage_and_publication_identity(self):
        study='11111111-1111-4111-8111-111111111111'
        publication={'id':'pub1','study_id':study,'status':'published','version':1,'record_ids':{k:[] for k in PUBLIC_KINDS if k!='publications'}}
        def rows(kind,params=None,public=False):
            self.assertTrue(public)
            if kind=='studies':return [{'id':study}]
            if kind=='publications':return [copy.deepcopy(publication)]
            return []
        with patch.object(store,'rows',side_effect=rows):
            result=store.snapshot(study);self.assertEqual(result['access'],'published-snapshot');self.assertEqual(result['coverage']['claims'],0)
        publication['record_ids']['claims']=['missing']
        with patch.object(store,'rows',side_effect=rows):
            with self.assertRaises(store.StoreError):store.snapshot(study)
    def test_byte_budget_does_not_return_a_truncated_snapshot(self):
        study='11111111-1111-4111-8111-111111111111'
        def rows(kind,params=None,public=False):return [{'id':study,'title':'x'*2000}] if kind=='studies' else []
        with patch.object(store,'rows',side_effect=rows):
            # Owner access has no publication requirement; the accumulated budget still applies.
            with self.assertRaises(store.StoreError):store.snapshot(study,owner=True,byte_budget=100)

if __name__=='__main__':unittest.main()
