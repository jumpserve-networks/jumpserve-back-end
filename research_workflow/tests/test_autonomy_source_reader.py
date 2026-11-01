"""Source reader budget regressions; no external services or authority changes."""
import copy,unittest,uuid,sys
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
from unittest.mock import patch
from test_autonomy_inputs import source_fixture
import autonomy,autonomy_inputs as inputs,store,workflow

class SourceReaderBudget(unittest.TestCase):
 def test_source_reread_does_not_repeat_initial_inline_bundle(self):
  # These papers fit the initial 16 KB transport but the duplicated bundle
  # either consumes the 10 KB reader budget or leaves no room for one row.
  for padding in ('x'*6000, '海🌍\\\"'*700):
   with self.subTest(padding_characters=len(padding)):
    t,c,s,raw,records,lookup,packet=source_fixture()
    packet['pages']=[dict(page=1,text=c['description']+' '+padding),dict(page=2,text='')]
    raw=workflow.canonical(packet);records['artifacts'][t['context']['text_artifact_id']]['sha256']=workflow.digest(raw)
    with patch.object(autonomy,'one',side_effect=lookup),patch.object(autonomy,'inventory',return_value=[]),patch.object(autonomy,'retained_jobs',return_value=[]),patch.object(store,'artifact_bytes',return_value=raw):
     material,expected=inputs.source_text(t,c,s,lookup)
     self.assertEqual(material['inline_fragments'],expected)
     self.assertLessEqual(len(workflow.canonical(expected)),inputs.INLINE_SOURCE_BYTES)
     original_material=copy.deepcopy(material);cursor=0;received=[]
     # Keep the exact source material stable while exercising reader paging.
     with patch.object(inputs,'source_text',return_value=(material,expected)):
      for _ in range(len(expected)+1):
       read=autonomy.inspect_evidence(t,dict(kind='source',id=s['id'],cursor=cursor));result=read['result']
       self.assertLessEqual(len(workflow.canonical(result)),autonomy.evidence.READ_BYTES)
       for key in ('inline_passage','inline_fragments','inline_fragment_coverage','inline_reading_guidance'):
        self.assertNotIn(key,result['source_material'])
       self.assertEqual(result['source_material']['text_artifact_sha256'],workflow.digest(raw))
       self.assertFalse(result['source_material']['complete_source_review'])
       self.assertEqual(result['page_sha256'],workflow.digest(workflow.canonical(result['records'])))
       received.extend(result['records']);inputs.retain_source_delivery(t,read)
       if result['next_cursor'] is None:break
       self.assertGreater(result['next_cursor'],cursor);cursor=result['next_cursor']
      else:self.fail('Short source reader did not terminate')
      self.assertEqual(received,expected)
      self.assertEqual(material,original_material)
      self.assertTrue(inputs.delivery_progress(t,material)['all_extracted_fragments_delivered'])
      # A completed inline delivery still permits an exact legitimate reread.
      reread=autonomy.inspect_evidence(t,dict(kind='source',id=s['id'],cursor=0))['result']
      self.assertEqual(reread['records'],expected[:reread['returned_records']])
      terminal=autonomy.inspect_evidence(t,dict(kind='source',id=s['id'],cursor=len(expected)))['result']
      self.assertEqual(terminal['records'],[]);self.assertIsNone(terminal['next_cursor'])
      with self.assertRaisesRegex(ValueError,'outside this claim'):
       autonomy.inspect_evidence(t,dict(kind='source',id=str(uuid.uuid4()),cursor=0))
