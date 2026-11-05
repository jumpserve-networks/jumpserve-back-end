"""Fresh warm leases initialize sources; retained partial work never restarts."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy as a
from test_autonomy import task


class IntakeInitialization(unittest.TestCase):
 def test_fresh_general_and_warm_papers_initialize_before_reading_text(self):
  for context in ({},{'intake_pool_version':'warm-intake-v1'}):
   with self.subTest(context=context):
    t=task('intake');t['context']=copy.deepcopy(context);t['intake_pool']=bool(context)
    study={'id':t['study_id']};original=dict(source_id='source',text_artifact_id='text',chunk_index=0)
    with patch.object(a,'one',return_value=study),patch.object(a,'rows',return_value=[{'status':'preparing'}]),patch.object(a.preparation,'registered',return_value=(None,None)),patch.object(a.sources,'original',return_value=original) as acquire,patch.object(a.store,'artifact_bytes') as read,patch.object(a.model,'call') as infer,patch.object(a,'advance') as advance:
     a.intake(t)
    acquire.assert_called_once_with(study,t['actor_id']);read.assert_not_called();infer.assert_not_called()
    self.assertEqual(advance.call_args.args[1],'queued')
    self.assertEqual(advance.call_args.kwargs['context'],dict(context,**original))
    self.assertEqual(t['context'],context);self.assertEqual(original['chunk_index'],0)

 def test_partial_context_and_unknown_markers_do_not_reinitialize_or_infer(self):
  for context in ({'intake_pool_version':'other'},{'intake_pool_version':'warm-intake-v1','last_turn_id':'retained'}, {'source_id':'retained-source'}):
   with self.subTest(context=context):
    t=task('intake');t['context']=copy.deepcopy(context)
    with patch.object(a,'one',return_value={'id':t['study_id']}),patch.object(a,'rows',return_value=[{'status':'preparing'}]),patch.object(a.sources,'original') as acquire,patch.object(a.model,'call') as infer,patch.object(a,'advance') as advance:
     with self.assertRaises(KeyError):a.intake(t)
    acquire.assert_not_called();infer.assert_not_called();advance.assert_not_called()
    self.assertEqual(t['context'],context)

 def test_initialized_warm_paper_admits_only_remaining_chunks(self):
  t=task('intake');t['intake_pool']=True
  t['context']=dict(intake_pool_version='warm-intake-v1',text_artifact_id='original-text',source_id='original-source',chunk_index=1,last_turn_id='retained-turn')
  original=copy.deepcopy(t['context']);packet=b'{"pages":[{"page":1,"text":"first"},{"page":2,"text":"second"}]}'
  import intake_parallel
  with patch.object(a,'one',return_value={'id':t['study_id']}),patch.object(a,'rows',return_value=[{'status':'preparing'}]),patch.object(a.sources,'original') as acquire,patch.object(a.store,'artifact_bytes',return_value=packet),patch.object(intake_parallel,'coordinate') as coordinate:
   a.intake(t)
  acquire.assert_not_called();self.assertEqual(coordinate.call_args.args[1],original)
  self.assertEqual(t['context'],original)


if __name__=='__main__':unittest.main()
