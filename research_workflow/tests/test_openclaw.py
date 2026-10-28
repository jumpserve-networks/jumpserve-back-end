"""Exposed OpenClaw protocol/dispatch regressions; synthetic provider, real CLI."""
import io,json,os,sys,tempfile,unittest,uuid
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import autonomy_model as model,model_dispatch as dispatch,openclaw_runtime as runtime,siwc_protocol as wire,workflow

ROOT=Path(__file__).resolve().parents[2]
def decision():return dict(action='blocked',reason='missing-required-inputs',blockers=[dict(kind='missing-data',detail='missing-source-data',required_input='Original input data')],claims=[],check_id=None,proposal=None,ready=None,coverage_complete=False,assessment_label=None,evidence_ids=[])
def request():
 result=model.request('Synthetic provider transport only',dict(task='planning'));result['modelId']=runtime.MODEL;return result
class Response(io.BytesIO):
 status=200;headers={}
 def read1(self,n):return self.read(n)
def stream():
 text=json.dumps(decision());return b'data: '+json.dumps(dict(type='response.completed',response=dict(status='completed',model=runtime.MODEL,output=[dict(type='message',role='assistant',content=[dict(type='output_text',text=text)])],usage=dict(input_tokens=100,output_tokens=30,total_tokens=130)))).encode()+b'\n\n'

class OpenClaw(unittest.TestCase):
 def test_complete_stream_after_former_deadline_is_accepted_only_with_new_bounded_window(self):
  class Slow(Response):
   def __init__(self):
    self.clock=0;self.timeout=0;self.frames=[b'data: {"type":"response.in_progress"}\n\n']*7+[stream()]
    self.fp=SimpleNamespace(raw=SimpleNamespace(_sock=self))
   def settimeout(self,seconds):self.timeout=seconds
   def read1(self,n):
    if not self.frames:return b''
    if 10>self.timeout:raise TimeoutError('Controlled blocking-read timeout')
    self.clock+=10
    return self.frames.pop(0)
  slow=Slow()
  with patch.object(wire.time,'monotonic',side_effect=lambda:slow.clock):
   with self.assertRaises(TimeoutError):wire.read_stream(slow,io.BytesIO(),65,10000)
  slow=Slow();sink=io.BytesIO()
  with patch.object(wire.time,'monotonic',side_effect=lambda:slow.clock):result=wire.read_stream(slow,sink,runtime.DATA_RESPONSE_SECONDS,10000)
  self.assertEqual(result['status'],'completed');self.assertEqual(slow.clock,80)
  self.assertIn(b'response.completed',sink.getvalue())
  self.assertLess(runtime.DATA_RESPONSE_SECONDS,runtime.DATA_CLI_SECONDS)
  self.assertLess(runtime.DATA_PROCESS_SECONDS,235);self.assertLess(235,dispatch.WAIT_SECONDS)
 def test_wire_has_explicit_json_input_and_never_requests_paid_keys_or_unsupported_fields(self):
  result=wire.response_request(request());self.assertEqual(result['input'][0]['role'],'developer');self.assertIn('JSON',result['input'][0]['content'])
  self.assertFalse(result['store']);self.assertTrue(result['stream']);self.assertEqual(result['tools'],[])
  for field in ('temperature','max_output_tokens','previous_response_id'):self.assertNotIn(field,result)
 def test_nonassessment_citations_are_rejected_and_phase_schema_requires_empty_fields(self):
  value=decision();value['evidence_ids']=['unrelated-prior-run']
  for phase in ('planning','critic','comprehension','discover'):
   with self.assertRaisesRegex(ValueError,'only applicable'):model.validate_phase(value,dict(task=phase))
   schema=model.request('Policy',dict(task=phase))['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']['properties']
   self.assertEqual(schema['evidence_ids']['maxItems'],0)
  value['evidence_ids']=[];model.validate_phase(value,dict(task='planning'))
 def test_missing_usage_is_not_zero_and_stream_terminal_is_required(self):
  self.assertIsNone(wire.usage({}));self.assertEqual(wire.usage(dict(usage=dict(input_tokens=0))),dict(inputTokens=0,outputTokens=None,totalTokens=None))
  sink=io.BytesIO();result=wire.read_stream(Response(stream()),sink,__import__('time').monotonic()+2,10000);self.assertEqual(sink.getvalue(),stream());self.assertEqual(result['status'],'completed')
  with self.assertRaises(wire.StreamFailure):wire.read_stream(Response(b'data: {"type":"response.output_text.delta"}\n\n'),io.BytesIO(),__import__('time').monotonic()+2,10000)
 def test_environment_excludes_ambient_credentials_and_other_endpoints(self):
  with patch.dict(os.environ,dict(OPENAI_API_KEY='forbidden',AWS_SECRET_ACCESS_KEY='forbidden',HTTPS_PROXY='forbidden',ANTHROPIC_API_KEY='forbidden')):env=runtime.environment(Path('/tmp/synthetic-openclaw'))
  for key in ('OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','HTTPS_PROXY','ANTHROPIC_API_KEY'):self.assertNotIn(key,env)
 def test_dispatch_has_one_durable_admission_and_preserves_stored_response(self):
  task=dict(study_id=str(uuid.uuid4()),id=str(uuid.uuid4()));turn=str(uuid.uuid4());raw=workflow.canonical(dict(output={},usage={}))
  result=dict(status='complete',response_artifact_id=str(uuid.uuid4()),response_sha256=workflow.digest(raw),provenance=dict(runtime=dispatch.VERSION))
  with patch.object(dispatch,'rpc',side_effect=[dict(replayed=False),result]) as rpc,patch.object(dispatch.store,'artifact_bytes',return_value=raw):answer=dispatch.invoke(task,turn,request())
  self.assertEqual([c.args[0] for c in rpc.call_args_list],['enqueue','result']);self.assertEqual(answer['usage']['research_runtime']['runtime'],dispatch.VERSION)
  with patch.object(dispatch,'rpc',return_value=dict(replayed=True)) as rpc,self.assertRaisesRegex(ValueError,'never replayed'):dispatch.invoke(task,turn,request())
  self.assertEqual(rpc.call_count,1)
 def test_paused_dispatch_does_not_fall_back_to_another_provider(self):
  task=dict(study_id=str(uuid.uuid4()),id=str(uuid.uuid4()))
  with patch.object(dispatch,'rpc',side_effect=[dict(replayed=False),dict(status='paused-quota',provenance=dict(paid_fallback=False))]),self.assertRaises(dispatch.DispatchError):dispatch.invoke(task,str(uuid.uuid4()),request())
 def test_real_native_cli_matches_retained_provider_decision(self):
  package=ROOT/'experiments/openclaw_pilot/runtime/node_modules/openclaw';node=package.parent/'node/bin/node'
  if not node.exists():self.skipTest('Pinned local native OpenClaw runtime absent; required deployed equivalent remains a release gate.')
  calls=[]
  def transport(method,url,token,body):calls.append(json.loads(body));return Response(stream())
  with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package):
   answer,receipt=runtime.execute(request(),Path(tmp)/'turn',token_source=lambda:'synthetic-not-a-credential',transport=transport)
   self.assertEqual(receipt['status'],'complete',receipt);self.assertEqual(model.parse(answer)['action'],'blocked');self.assertEqual(len(calls),1)
   self.assertEqual((Path(tmp)/'turn/provider-response.bin').read_bytes(),stream());self.assertFalse(receipt['paid_fallback'])

if __name__=='__main__':unittest.main()
