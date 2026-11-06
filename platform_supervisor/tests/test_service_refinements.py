"""Follow-up requirements and safe observation views retain separate identities."""
import copy,json,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'research_workflow'))
from platform_supervisor import service_work as service
from platform_supervisor.common import canonical,digest,Hold
def uid():return str(uuid.uuid4())
class Refinements(unittest.TestCase):
 def test_changed_service_requirements_stop_once_without_reactivating_the_original(self):
  spec=dict(version=service.VERSION,profile='assessment-evidence-v1',objective='Inspect the retained original spool.',acceptance=['Keep uncertainty.'])
  state=dict(pending_service_work=dict(spec=spec),deadline_at='original-deadline',committed_cents=1700)
  store=Mock();store.worker=uid();store.task=dict(id=uid(),lease_token=uid());store.artifact.side_effect=lambda name,raw:dict(id=uid(),sha256=digest(raw))
  result=dict(id=uid(),implementation_task_id=uid(),profile=spec['profile'],requires_implementation=True,refinement_spec_sha256=digest(canonical(spec)),reason='Read the worker observation and implement any remaining adapter.')
  store.request.side_effect=[dict(version=service.VERSION,evidence=None,activation=None,implementation=None),result]
  c=SimpleNamespace(store=store,state=state,flags=dict(service_work_contract=service.VERSION),save_state=Mock())
  with tempfile.TemporaryDirectory() as tmp,self.assertRaises(Hold) as stopped:service.deliver(c,Path(tmp))
  self.assertEqual(stopped.exception.status,'blocked');self.assertNotIn('pending_service_work',state);self.assertNotIn('service_work_wait',state)
  self.assertTrue(state['service_refinement']['requires_implementation']);self.assertEqual(state['deadline_at'],'original-deadline');self.assertEqual(state['committed_cents'],1700)
  self.assertIn('not proof',state['feedback'])
  with tempfile.TemporaryDirectory() as tmp:
   progress=Path(tmp)/'progress.json';raw=canonical(dict(version='platform-progress-v1',ready=False,status='blocked',service_work=spec));progress.write_bytes(raw)
   self.assertFalse(service.capture(Path(tmp),state));self.assertEqual(progress.read_bytes(),raw);self.assertNotIn('pending_service_work',state)
   changed=dict(spec,objective='Additional exact implementation requirement.')
   progress.write_bytes(canonical(dict(version='platform-progress-v1',ready=False,status='blocked',service_work=changed)))
   with self.assertRaises(Hold):service.capture(Path(tmp),state)
   self.assertEqual(state['pending_service_work']['spec'],changed)
 def test_source_view_labels_original_and_redacted_hashes_separately(self):
  study,task,ident=uid(),uid(),uid();raw=b'passive source\nBearer synthetic-secret-token-value-1234567890\n';h=digest(raw)
  row=dict(id=ident,sha256=h,byte_count=len(raw),created_at='2026-11-06T00:00:00Z',provenance=dict(task_id=task,kind='worker-installed-source-v1',filename='autonomy.py'))
  store=Mock();store.request.return_value=raw;c=SimpleNamespace(store=store)
  with tempfile.TemporaryDirectory() as tmp:
   result=service.runtime_observations(c,Path(tmp),dict(assessment_id=study,assessment_task_id=task,runtime_observations=[row]))
   output=(Path(tmp)/result[0]['view_path']).read_bytes();view=json.loads(output)
  self.assertEqual(result[0]['original_sha256'],h);self.assertEqual(result[0]['view_sha256'],digest(output))
  self.assertEqual(view['original_sha256'],h);self.assertNotIn(b'synthetic-secret-token-value',output)
 def test_cross_task_oversized_and_changed_original_observations_fail_closed(self):
  study,task=uid(),uid();raw=b'passive';row=dict(id=uid(),sha256=digest(raw),byte_count=len(raw),provenance=dict(task_id=task,kind='worker-installed-source-v1'))
  for defect in ('task','size','hash'):
   with self.subTest(defect=defect),tempfile.TemporaryDirectory() as tmp:
    broken=copy.deepcopy(row);store=Mock();store.request.return_value=raw
    if defect=='task':broken['provenance']['task_id']=uid()
    elif defect=='size':broken['byte_count']=262145
    else:store.request.return_value=raw+b'changed'
    with self.assertRaises(Hold):service.runtime_observations(SimpleNamespace(store=store),Path(tmp),dict(assessment_id=study,assessment_task_id=task,runtime_observations=[broken]))
    self.assertEqual(list(Path(tmp).rglob('*.json')),[])
if __name__=='__main__':unittest.main()
