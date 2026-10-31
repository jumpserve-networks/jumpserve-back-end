"""Additional expired-turn safety regressions; existing tests are unchanged."""
import copy,unittest,uuid
from unittest.mock import patch
from test_autonomous_repair import task,repair,workflow
class ExpiredTurns(unittest.TestCase):
 def test_expired_controller_turn_without_dispatch_remains_fenced(self):
   t=task();turn=dict(id=str(uuid.uuid4()),study_id=t['study_id'],task_id=t['id'],status='unknown',request_sha256='a'*64)
   def rows(task,kind,**query):
    return [turn] if kind=='autonomy_turns' and query.get('status')=='eq.unknown' else []
   import model_dispatch
   with patch.object(repair,'rows',side_effect=rows),patch.object(model_dispatch,'invoke') as invoke:
    result=repair.diagnose(t,dict(category='runtime'))
   self.assertTrue(result['waiting']);self.assertIn(turn['id'],result['reason']);invoke.assert_not_called()

 def test_expired_turn_coverage_overflow_fails_closed(self):
   def rows(task,kind,**query):
    return [dict(id=str(uuid.uuid4())) for _ in range(101)] if kind=='autonomy_turns' and query.get('status')=='eq.unknown' else []
   with patch.object(repair,'rows',side_effect=rows),patch.object(repair,'original') as original:
    with self.assertRaisesRegex(ValueError,'coverage exceeds'):repair.diagnose(task(),dict(category='runtime'))
   original.assert_not_called()

 def test_expired_turn_with_unconfirmed_dispatch_still_waits(self):
   t=task();turn=dict(id=str(uuid.uuid4()),study_id=t['study_id'],task_id=t['id'],status='unknown',request_sha256='a'*64)
   dispatch=dict(turn,provenance={})
   def rows(task,kind,**query):
    if kind=='autonomy_turns' and query.get('status')=='eq.unknown':return [turn]
    return [dispatch] if kind=='model_dispatches' else []
   with patch.object(repair,'rows',side_effect=rows):result=repair.diagnose(t,dict(category='runtime'))
   self.assertTrue(result['waiting']);self.assertIn(turn['id'],result['reason'])

 def test_expired_turn_requires_exact_dispatch_request_identity(self):
   t=task();identity=str(uuid.uuid4())
   turn=dict(id=identity,study_id=t['study_id'],task_id=t['id'],status='unknown',request_sha256='a'*64)
   dispatch=dict(turn,status='complete',request_sha256='b'*64,response_artifact_id=str(uuid.uuid4()),response_sha256=workflow.digest(b'{}'),provenance={})
   def rows(task,kind,**query):
    if kind=='autonomy_turns' and query.get('status')=='eq.unknown':return [turn]
    return [dispatch] if kind=='model_dispatches' else []
   with patch.object(repair,'rows',side_effect=rows),patch.object(repair,'original',return_value=b'{}'):
    with self.assertRaisesRegex(ValueError,'turn.*identity mismatch'):repair.diagnose(t,dict(category='runtime'))

 def test_expired_turn_outside_recent_window_reconciles_original_response(self):
   t=task();turn=dict(id=str(uuid.uuid4()),study_id=t['study_id'],task_id=t['id'],status='unknown',request_sha256='a'*64)
   dispatch=dict(turn,status='complete',response_artifact_id=str(uuid.uuid4()),response_sha256=workflow.digest(b'{}'),provenance={})
   before=copy.deepcopy(turn);queries=[]
   def rows(task,kind,**query):
    if kind=='autonomy_turns' and query.get('status')=='eq.unknown':return [turn]
    if kind=='model_dispatches':
     queries.append(query)
     return [dispatch] if query.get('id')=='in.('+turn['id']+')' else []
    return []
   with patch.object(repair,'rows',side_effect=rows),patch.object(repair,'original',return_value=b'{}') as original:
    result=repair.diagnose(t,dict(category='runtime'))
   self.assertFalse(result['waiting']);self.assertFalse(result['validated']);self.assertEqual(turn,before)
   self.assertEqual(result['reconciled_dispatches'][0]['id'],turn['id']);original.assert_called_once()
   self.assertTrue(any(q.get('id') for q in queries))
