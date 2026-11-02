"""Qualify and release supervisor operational repairs through a frozen broker."""
import importlib.util,json
from pathlib import Path
from . import source
from .common import canonical,POLICY_SHA256,Hold

VERSION='platform-supervisor-self-repair-v1'
BACKEND='jumpserve-networks/jumpserve-back-end';INFRA='jumpserve-networks/jumpserve-infra'
ROOT=Path(__file__).resolve().parents[1]
def gate():
 paths=(ROOT/'supervisor-runtime-gate.py',ROOT.parent/'supervisor-runtime-gate.py',
  ROOT/'.test-artifacts/supervisor-gate/assets/platform/supervisor-runtime-gate.py',
  ROOT.parent/'jumpserve-infra/assets/platform/supervisor-runtime-gate.py')
 path=next((p for p in paths if p.is_file()),None)
 if path is None:raise Hold('Frozen supervisor scope gate is unavailable; install the reviewed broker asset before qualification.')
 spec=importlib.util.spec_from_file_location('frozen_supervisor_gate',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def capabilities():
 module=gate()
 return dict(version=VERSION,component='supervisor-runtime',workflow='supervisor-runtime-release.yml',
  scope='Operational supervisor Python modules through frozen qualification; default protected paths still apply to ordinary publication.',
  frozen_files=sorted(module.KERNEL),frozen_methods={k:sorted(v) for k,v in module.METHODS.items()},
  requires_further_owner_approval=False,qualification_and_installed_source_proof_required=True)
def prepare(controller,workspace,directory):
 original=controller.state['sources'][BACKEND]['snapshot'];current=source.snapshot(directory)
 if not any(p.startswith('platform_supervisor/') and original.get(p)!=current.get(p) for p in set(original)|set(current)):return None
 changed=[p for p in set(original)|set(current) if original.get(p)!=current.get(p)]
 if any(not p.startswith(('platform_supervisor/','docs/')) for p in changed):raise Hold('Supervisor repairs require an isolated operational source change.')
 root=workspace.parent/'supervisor-qualification'
 manifest=controller.store.request('/rest/v1/rpc/platform_supervisor_runtime','POST',{})
 if not manifest:raise Hold('Verified supervisor activation is unavailable; retain this self-repair for the qualified runtime broker.')
 previous=root/manifest['source_revision']/'backend'
 if not previous.exists():source.extract(controller.github.archive(BACKEND,manifest['source_revision']),previous)
 try:scope=gate().inspect(previous/'platform_supervisor',directory/'platform_supervisor',manifest)
 except ValueError as error:raise Hold('Supervisor qualification: '+str(error)) from None
 controller.state['supervisor_runtime']=dict(scope=scope,previous_manifest=manifest)
 return scope
def qualify(controller,directory,scope,receipt,artifact):
 if receipt.get('passed') is not True:raise Hold('Supervisor update requires executed fixed checks.')
 # Protected branch CI repeats all existing tests at the exact proposed head;
 # the release workflow additionally runs the predecessor's frozen tests.
 certificate=dict(version=VERSION,scope=scope,checks_artifact_id=artifact['id'],checks_sha256=artifact['sha256'],passed=True,
  policy_sha256=POLICY_SHA256,scientific_completion=False)
 proof=controller.store.artifact('supervisor-runtime-qualification.json',canonical(certificate))
 controller.state['supervisor_runtime'].update(certificate=certificate,certificate_artifact=proof['id']);controller.save_state()
 controller.store.event('supervisor-runtime-qualified','Operational supervisor repair passed the frozen scope gate and executed checks; protected CI and installed-source verification remain required.',dict(source_binding=scope['source_binding'],artifact_id=proof['id']))
def release_request(controller,change):
 state=controller.state.get('supervisor_runtime') or {}
 if not state.get('certificate',{}).get('passed'):raise Hold('Frozen supervisor qualification is unavailable.')
 revision=controller.state.setdefault('release_workflow_revisions',{}).setdefault(INFRA,controller.github.main(INFRA))
 return dict(repository=BACKEND,revision=change['merge_sha'],component='supervisor-runtime',workflow_revision=revision,
  qualification_artifact_id=state['certificate_artifact'],previous_revision=state['previous_manifest']['source_revision'])
