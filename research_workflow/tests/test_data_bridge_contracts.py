"""Loopback bridge and durable outbox boundaries without provider calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import model_dispatch as dispatch
import openclaw_runtime as runtime
import store
from workflow import canonical,digest
import test_openclaw as fixtures


class DataBridgeContracts(unittest.TestCase):
 def test_truncated_body_is_rejected_before_a_provider_operation(self):
  answer,receipt,calls,errors,_=fixtures.OpenClaw().local_bridge(incomplete=True)
  self.assertIsNone(answer);self.assertEqual(calls,[]);self.assertEqual(errors[0]['error']['code'],'native-incomplete-body');self.assertEqual(receipt['error_code'],'native-incomplete-body');self.assertFalse(receipt['provider_request_sent'])

 def test_changed_cli_answer_cannot_replace_the_retained_provider_decision(self):
  answer,receipt,calls,errors,artifacts=fixtures.OpenClaw().local_bridge(final_text=lambda _: '{}')
  self.assertIsNone(answer);self.assertEqual(receipt['status'],'invalid-output');self.assertEqual(len(calls),1);self.assertEqual(errors,[])
  self.assertEqual(artifacts['provider-response.bin'],fixtures.stream());self.assertEqual(json.loads(artifacts['stdout.bin'])['payloads'][0]['text'],'{}')

 def test_failed_completion_preserves_originals_without_inventing_a_decision(self):
  with tempfile.TemporaryDirectory() as tmp:
   directory=Path(tmp);raw=canonical({'original':'request','missing':None,'zero':0});(directory/'logical.json').write_bytes(raw)
   job=dict(id=str(uuid.uuid4()),study_id=str(uuid.uuid4()),actor_id=str(uuid.uuid4()),lease_token='original-fence',request_sha256=digest(raw))
   receipt=dict(status='failed',provider_calls=0,response_received=False);runtime.save_completion(job,directory,None,receipt,directory/'pending')
   records=[]
   def artifact(study,actor,raw,identity,kind,metadata):
    records.append((raw,metadata));return dict(id=identity,sha256=digest(raw),byte_count=len(raw))
   with patch.object(dispatch,'rpc',side_effect=[{'status':'running'},{}]) as rpc,patch.object(store,'artifact',side_effect=artifact),patch.object(store,'append'):
    self.assertTrue(runtime.persist_completed(job,directory))
   self.assertTrue(records);self.assertTrue(all(metadata['kind']=='model-runtime-original' for _,metadata in records));self.assertIn(raw,[data for data,_ in records])
   finish=rpc.call_args.args[1];self.assertEqual(finish['status'],'failed');self.assertIsNone(finish['response_artifact_id']);self.assertIsNone(finish['response_sha256'])
   (directory/'logical.json').write_bytes(b'changed')
   with patch.object(dispatch,'rpc') as rpc,patch.object(store,'artifact') as upload,self.assertRaisesRegex(ValueError,'original request hash differs'):
    runtime.persist_completed(job,directory)
   rpc.assert_not_called();upload.assert_not_called()

 def test_outbox_never_follows_a_symlink_to_a_private_completion(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);pending=root/'pending-completions';pending.mkdir();original=root/'private.json';original.write_bytes(b'Original private bytes');(pending/'foreign.json').symlink_to(original)
   with patch.object(runtime,'persist_completed') as persist,self.assertRaisesRegex(ValueError,'cannot follow symlinks'):runtime.retry_completions(root)
   persist.assert_not_called();self.assertEqual(original.read_bytes(),b'Original private bytes')


if __name__=='__main__':unittest.main()
