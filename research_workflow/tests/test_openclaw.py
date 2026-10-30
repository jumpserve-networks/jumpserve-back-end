"""Exposed OpenClaw protocol/dispatch regressions; synthetic provider, real CLI."""
import io,json,os,sys,tempfile,unittest,uuid,urllib.error,urllib.request
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
 def local_bridge(self,mutate=lambda request:None,transport=None,repeats=1):
  """Real loopback HTTP handler, explicitly emulated native CLI and upstream."""
  calls=[];errors=[]
  def upstream(method,url,token,body):
   calls.append(body)
   return transport(method,url,token,body) if transport else Response(stream())
  def cli(arguments,**kwargs):
   cfg=json.loads(Path(arguments[arguments.index('--config')+1]).read_bytes())
   settings=cfg['models']['providers']['lab'];message=Path(arguments[arguments.index('--message-file')+1]).read_text()
   value=dict(model=runtime.MODEL,messages=[dict(role='user',content=message)],stream=False);mutate(value)
   opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));output=None
   for _ in range(repeats):
    request=urllib.request.Request(settings['baseUrl']+'/chat/completions',data=workflow.canonical(value),headers={'Content-Type':'application/json','Authorization':'Bearer '+settings['apiKey']})
    try:
     with opener.open(request,timeout=3) as response:output=json.loads(response.read())['choices'][0]['message']['content']
    except urllib.error.HTTPError as error:errors.append(json.loads(error.read()))
   return SimpleNamespace(returncode=1 if output is None else 0,stdout=workflow.canonical(dict(payloads=[] if output is None else [dict(text=output)])),stderr=b'')
  with tempfile.TemporaryDirectory() as tmp,patch.object(runtime.subprocess,'run',side_effect=cli):
   directory=Path(tmp)/'turn';answer,receipt=runtime.execute(request(),directory,token_source=lambda:'synthetic',transport=upstream)
   artifacts={p.name:p.read_bytes() for p in directory.iterdir() if p.is_file() and p.name!='config.json'}
  return answer,receipt,calls,errors,artifacts

 def test_hash_bound_handoff_keeps_exact_provider_content_and_original_prompt(self):
  answer,receipt,calls,errors,artifacts=self.local_bridge()
  self.assertEqual(receipt['status'],'complete');self.assertEqual(errors,[]);self.assertEqual(len(calls),1)
  self.assertEqual(calls[0],workflow.canonical(wire.response_request(request())))
  self.assertEqual(artifacts['message.json'].decode(),request()['messages'][0]['content'][0]['text'])
  handoff=json.loads(artifacts['native-message.json']);self.assertEqual(handoff['reference'],'controller-retained-request-sha256:'+workflow.digest(artifacts['logical.json']))
  self.assertEqual(receipt['context_transport']['original_logical_sha256'],workflow.digest(artifacts['logical.json']))
  self.assertEqual(receipt['request_sizes']['provider_bytes'],len(calls[0]))

 def test_private_bridge_rejections_identify_exact_field_without_provider_calls(self):
  cases=[('native-model-mismatch','native.model',lambda r:r.update(model='wrong')),
         ('native-tools-forbidden','native.tools',lambda r:r.update(tools=[{'name':'forbidden'}])),
         ('native-message-role','native.messages[].role',lambda r:r['messages'][0].update(role='tool')),
         ('native-logical-reference','native.messages',lambda r:r['messages'][0].update(content='Unbound content')),
         ('native-bridge-byte-limit','native.content_length',lambda r:r.update(extra='x'*96000))]
  for code,field,mutation in cases:
   with self.subTest(code=code):
    answer,receipt,calls,errors,_=self.local_bridge(mutation)
    self.assertIsNone(answer);self.assertEqual(calls,[]);self.assertEqual(receipt['status'],'failed')
    self.assertFalse(receipt['provider_request_sent']);self.assertEqual(receipt['provider_calls'],0)
    self.assertEqual(receipt['error_code'],code);self.assertEqual(receipt['validation']['field'],field)
    self.assertEqual(errors[0]['error']['code'],code);self.assertEqual(errors[0]['error']['param'],field)
    if code=='native-bridge-byte-limit':self.assertGreater(receipt['validation']['observed'],receipt['validation']['limit'])

 def test_unknown_provider_outcome_is_not_replayed_or_rewritten_by_second_request(self):
  def failure(*_):raise wire.StreamFailure('provider-wall-limit','unknown')
  answer,receipt,calls,errors,_=self.local_bridge(transport=failure,repeats=2)
  self.assertIsNone(answer);self.assertEqual(len(calls),1);self.assertEqual(receipt['status'],'unknown')
  self.assertTrue(receipt['provider_request_sent']);self.assertEqual(receipt['provider_calls'],1)
  self.assertEqual(receipt['error_code'],'provider-wall-limit');self.assertEqual(receipt['validation']['code'],'retained-provider-operation')
  self.assertEqual([e['error']['code'] for e in errors],['provider-wall-limit','retained-provider-operation'])

 def test_invalid_logical_request_is_retained_before_credential_or_native_work(self):
  for logical,code in ((dict(request(),modelId='wrong'),'logical-model-mismatch'),(dict(request(),extra='x'*96000),'logical-request-byte-limit')):
   with self.subTest(code=code),tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'credential') as credential,patch.object(runtime.subprocess,'run') as cli:
    directory=Path(tmp)/'turn';answer,receipt=runtime.execute(logical,directory,token_source=credential)
    self.assertIsNone(answer);credential.assert_not_called();cli.assert_not_called()
    self.assertEqual(receipt['error_code'],code);self.assertEqual(receipt['provider_calls'],0)
    self.assertEqual((directory/'logical.json').read_bytes(),workflow.canonical(logical));self.assertEqual(json.loads((directory/'receipt.json').read_bytes())['validation']['code'],code)

 def test_public_validation_reason_is_precise_without_echoing_untrusted_values(self):
  provenance=dict(error_code='native-bridge-byte-limit',provider_request_sent=False,provider_calls=0,validation=dict(version='openclaw-request-validation-v1',code='native-bridge-byte-limit',field='secret-token-injected',rule='private-prompt-injected',observed=102035,limit=96000))
  reason=dispatch.DispatchError(dict(status='failed',provenance=provenance)).public_reason
  self.assertIn('102035 bytes; limit 96000 bytes',reason);self.assertIn('No model request was sent',reason)
  self.assertNotIn('secret-token',reason);self.assertNotIn('private-prompt',reason)
  for changes in (dict(provider_request_sent=True),dict(provider_calls=1),dict(error_code='arbitrary-source-instruction')):
   self.assertNotIn('No model request was sent',dispatch.DispatchError(dict(status='failed',provenance=dict(provenance,**changes))).public_reason)
  self.assertNotIn('No model request was sent',dispatch.DispatchError(dict(status='unknown',provenance=provenance)).public_reason)

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
  calls=[];logical=request()
  def transport(method,url,token,body):calls.append(json.loads(body));return Response(stream())
  with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package):
   answer,receipt=runtime.execute(logical,Path(tmp)/'turn',token_source=lambda:'synthetic-not-a-credential',transport=transport)
   self.assertEqual(receipt['status'],'complete',receipt);self.assertEqual(model.parse(answer)['action'],'blocked');self.assertEqual(len(calls),1)
   self.assertEqual((Path(tmp)/'turn/provider-response.bin').read_bytes(),stream());self.assertFalse(receipt['paid_fallback'])
   self.assertEqual(calls[0],wire.response_request(logical));self.assertEqual(receipt['provider_calls'],1);self.assertTrue(receipt['provider_request_sent'])

 def test_real_cli_preflight_rejection_is_confirmed_zero_provider_calls(self):
  package=ROOT/'experiments/openclaw_pilot/runtime/node_modules/openclaw';node=package.parent/'node/bin/node'
  if not node.exists():self.skipTest('Pinned native CLI absent; equivalent native release check remains required.')
  from unittest.mock import Mock
  transport=Mock(side_effect=AssertionError('Provider must not be contacted'))
  # Freeze a valid admitted logical request before injecting a larger runtime
  # wire body. In production's OPENCLAW mode, request() also preflights that
  # body and would otherwise stop before this runtime guard is exercised.
  logical=request()
  with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'NODE',node),patch.object(runtime,'PACKAGE',package),patch.object(wire,'response_request',return_value={'oversize':'x'*96000}):
   with patch.object(model,'OPENCLAW',True),patch.object(model,'MODEL',runtime.MODEL),self.assertRaisesRegex(ValueError,'96 KB'):request()
   answer,receipt=runtime.execute(logical,Path(tmp)/'turn',token_source=lambda:'synthetic-not-a-credential',transport=transport)
   self.assertIsNone(answer);self.assertEqual(receipt['status'],'failed');self.assertEqual(receipt['error_code'],'provider-request-byte-limit');self.assertEqual(receipt['provider_calls'],0);self.assertFalse(receipt['provider_request_sent']);transport.assert_not_called()

if __name__=='__main__':unittest.main()
