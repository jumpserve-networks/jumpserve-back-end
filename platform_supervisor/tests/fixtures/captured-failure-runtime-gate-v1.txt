"""Separate source gate for captured failures and read-only runtime observations.

The existing operational gate is unchanged. Only the retained-result recovery
module can change; scientific requests, transport, tools and executors stay exact.
"""
import ast,hashlib,importlib.util,json,shutil
from pathlib import Path
VERSION='research-controller-recovery-v1'
PROFILE='captured-provider-failures-v1'
CHECKS='captured-failure-runtime-checks.py'
APPROVAL='policies/autonomous-repair-release-v1.json'
OLD_FUNCTIONS={'reconciled_call','ledger','reconcile_native_calls','confirmed','_reconcile_dispatches'}
NEW_FUNCTIONS={'captured_failure','controller_failure','runtime_diagnostic','recovery_artifact'}
OVERLAY='captured-provider-overlay-v1'
TEST='tests/test_captured_provider_failures.py'
class ScopeError(ValueError):pass
def digest(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def load(path):
 path=Path(path)
 if path.is_symlink() or not path.is_file():raise ScopeError('Plain exact runtime source required.')
 return path.read_bytes()
def normalized(raw):
 tree=ast.parse(raw);nodes=[]
 for n in tree.body:
  if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FAILURE_VERSION' for t in n.targets):
   if len(n.targets)!=1 or ast.literal_eval(n.value)!='captured-provider-failure-v1':raise ScopeError('Unexpected failure proof contract.')
   continue
  if isinstance(n,ast.FunctionDef) and n.name in OLD_FUNCTIONS|NEW_FUNCTIONS:
   if n.decorator_list or n.args.defaults or any(v is not None for v in n.args.kw_defaults) or n.returns:
    raise ScopeError('Import-time recovery effects require separate qualification.')
   for call in (v for v in ast.walk(n) if isinstance(v,ast.Call)):
    name=call.func.id if isinstance(call.func,ast.Name) else call.func.attr if isinstance(call.func,ast.Attribute) else ''
    if name in ('exec','eval','compile','credential','token_source','http_request','urlopen','Popen'):
     raise ScopeError('Inference, credentials or arbitrary execution are outside captured-result recovery.')
   if n.name in NEW_FUNCTIONS:continue
   n.body=[ast.Pass()]
  nodes.append(n)
 tree.body=nodes;return ast.dump(tree,include_attributes=False)
def inspect(before,after,manifest):
 before,after=Path(before),Path(after);files={};changed=[]
 if not isinstance(manifest.get('files'),dict) or not manifest['files']:raise ScopeError('Bound installed predecessor required.')
 for name,h in manifest['files'].items():
  old,new=load(before/name),load(after/name)
  if digest(old)!=h:raise ScopeError('Installed predecessor source differs: '+name)
  files[name]=digest(new)
  if name==APPROVAL or old==new:continue
  if name!='dispatch_recovery.py':raise ScopeError('Scientific/request/transport source cannot change in this profile: '+name)
  if normalized(old)!=normalized(new):raise ScopeError('Unrelated recovery or import-time source changed.')
  changed.append(name)
 if changed!=['dispatch_recovery.py']:raise ScopeError('One exact captured-result recovery change required.')
 for p in (before/'tests').rglob('*.py'):
  if p.is_symlink() or load(p)!=load(after/'tests'/p.relative_to(before/'tests')):raise ScopeError('Existing research checks changed.')
 old_names={p.name for p in before.iterdir() if p.is_file() and p.suffix in ('.py','.mjs')}
 if {p.name for p in after.iterdir() if p.is_file() and p.suffix in ('.py','.mjs')}-old_names:raise ScopeError('Undeclared executable runtime module added.')
 previous=digest(encoded({n:h for n,h in manifest['files'].items() if n!=APPROVAL}));approval=json.loads(load(before/APPROVAL))
 if approval.get('approved_for_lab_recovery') is not True or approval.get('qualified_files_sha256')!=previous:raise ScopeError('Inherited model evidence does not bind its original source.')
 return dict(version=VERSION,qualification_profile=PROFILE,changed_files=changed,files=files,
  source_binding=digest(encoded({n:h for n,h in files.items() if n!=APPROVAL})),previous_source_revision=manifest['source_revision'],previous_source_binding=previous,
  inherited_model_evidence_sha256=approval['evidence_sha256'],scientific_and_request_content_unchanged=True,existing_tests_unchanged=True,new_provider_calls=0)
def approval(scope,certificate_sha256):
 if scope.get('qualification_profile')!=PROFILE or scope.get('scientific_and_request_content_unchanged') is not True or len(certificate_sha256)!=64:raise ScopeError('Exact captured-failure qualification required.')
 return dict(version='autonomous-repair-release-v1',approved_for_lab_recovery=True,qualified_files_sha256=scope['source_binding'],evidence_sha256=certificate_sha256)
def pin(previous,revision,files):
 if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision) or set(files)!=set(previous['files']):raise ScopeError('Exact reviewed runtime and unchanged file coverage required.')
 return dict(version=previous['version']+1,source_revision=revision,files=dict(sorted(files.items())))
def overlay(previous):
 inherited=previous.get('source_overlay')
 if inherited is not None and (inherited.get('version')!=OVERLAY or inherited.get('paths')!=['dispatch_recovery.py',APPROVAL] or inherited.get('tests')!=[TEST]):
  raise ScopeError('Unknown inherited runtime source composition.')
 revision=inherited['base_revision'] if inherited else previous['source_revision']
 if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):raise ScopeError('Exact frozen base revision required.')
 return dict(version=OVERLAY,base_revision=revision,paths=['dispatch_recovery.py',APPROVAL],tests=[TEST])
def compose(base,proposal,target,include_test=True):
 """Build only fixed reviewed paths on a frozen base; no other main changes."""
 base,proposal,target=Path(base),Path(proposal),Path(target)
 if target.exists():raise ScopeError('A composed candidate cannot replace an existing tree.')
 target.mkdir(parents=True)
 shutil.copytree(base/'research_workflow',target/'research_workflow',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 for name in ['dispatch_recovery.py',APPROVAL,*([TEST] if include_test else [])]:
  origin=proposal/'research_workflow'/name;destination=target/'research_workflow'/name
  raw=load(origin);destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
 return target
