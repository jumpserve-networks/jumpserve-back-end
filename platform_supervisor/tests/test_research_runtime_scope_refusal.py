"""Scope refusals are blocked prerequisites, not uncertain supervisor failures."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from platform_supervisor import research_runtime
from platform_supervisor.common import Hold, canonical, digest


# Exact passive snapshot of assets/platform/research-runtime-gate.py for backend-only
# fixed checks. Never installed or selected by the production gate loader.
FROZEN_GATE_SHA256 = 'f11b0be9b3a89da91edaae11ee0d0b18761f5179566e35612773f31c9004a337'
FROZEN_GATE_SOURCE = b'"""Trusted deterministic eligibility gate for operational controller releases.\n\nScientific methods, model request construction, executors, prompts, policies,\ndependencies and existing research tests cannot change through this adapter.\nCandidate code is never imported by the credentialed gate.\n"""\nimport ast,copy,hashlib,json\nfrom pathlib import Path\nVERSION=\'research-controller-recovery-v1\'\nAPPROVAL=\'policies/autonomous-repair-release-v1.json\'\nFUNCTIONS={\n \'autonomy.py\':{\'handler\'},\n \'autonomous_repair.py\':{\'diagnose\',\'discover\'},\n \'autonomy_model.py\':{\'inspection_identifier\'},\n \'dispatch_recovery.py\':{\'ledger\',\'confirmed\',\'reconcile_spool\',\'_reconcile_dispatches\'},\n}\ndef canonical(value):return json.dumps(value,sort_keys=True,separators=(\',\',\':\')).encode()\ndef digest(raw):return hashlib.sha256(raw).hexdigest()\nclass ScopeError(ValueError):pass\ndef load(path):\n path=Path(path)\n if path.is_symlink() or not path.is_file():raise ScopeError(\'Plain declared source required: \'+str(path))\n return path.read_bytes()\nclass InspectionBoundary(ast.NodeTransformer):\n def visit_Expr(self,node):\n  expected=ast.parse(\'inspection_identifier(read)\').body[0]\n  if ast.dump(node,include_attributes=False)==ast.dump(expected,include_attributes=False):return None\n  return self.generic_visit(node)\nclass SourceEnvelopeBoundary(ast.NodeTransformer):\n """Permit only omission of duplicated initial inline transport in rereads."""\n def visit_FunctionDef(self,node):\n  if node.name!=\'inspect_evidence\':return node\n  assignment=ast.parse("inline_fields=(\'inline_passage\',\'inline_fragments\',\'inline_fragment_coverage\',\'inline_reading_guidance\')").body[0]\n  before=ast.parse("envelope=dict(source_material={k:v for k,v in material.items() if k!=\'inline_passage\'},record_kind=\'extracted-paper-text\' if extracted else \'source-metadata-only\')").body[0]\n  after=ast.parse("envelope=dict(source_material={k:v for k,v in material.items() if k not in inline_fields},record_kind=\'extracted-paper-text\' if extracted else \'source-metadata-only\')").body[0]\n  class Normalize(ast.NodeTransformer):\n   def visit_Assign(self,n):\n    shape=ast.dump(n,include_attributes=False)\n    if shape==ast.dump(assignment,include_attributes=False):return None\n    if shape==ast.dump(after,include_attributes=False):return copy.deepcopy(before)\n    return self.generic_visit(n)\n  # Both exact nodes are required; a differently scoped/changed field list\n  # or any other reader/auth/coverage edit remains frozen by the final AST.\n  shapes=[ast.dump(n,include_attributes=False) for n in ast.walk(node)]\n  if ast.dump(assignment,include_attributes=False) in shapes and ast.dump(after,include_attributes=False) in shapes:\n   return Normalize().visit(node)\n  return node\ndef protected_tree(raw,name):\n tree=ast.parse(raw)\n if name==\'autonomy.py\':tree=SourceEnvelopeBoundary().visit(tree)\n for node in tree.body:\n  if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in FUNCTIONS.get(name,set()):node.body=[ast.Pass()]\n  elif name==\'autonomy_model.py\' and isinstance(node,ast.FunctionDef) and node.name==\'parse\':InspectionBoundary().visit(node)\n  elif name==\'autonomy_model.py\' and isinstance(node,ast.ImportFrom) and node.module==\'workflow\':node.names=[n for n in node.names if n.name!=\'identifier\']\n # Only these fixed service RPC routes may be added to store routing.\n if name==\'store.py\':\n  allowed=[ast.dump(ast.parse("if name=="+repr(route)+":return request(\'/rest/v1/rpc/\'+name,\'POST\',payload)").body[0],include_attributes=False)\n   for route in (\'research_autonomy_runtime_observe\',\'research_controller_dispatch_reconcile\')]\n  for node in tree.body:\n   if isinstance(node,ast.FunctionDef) and node.name==\'rpc\':node.body=[n for n in node.body if ast.dump(n,include_attributes=False) not in allowed]\n # An operational helper may be added without changing any other module node.\n tree.body=[n for n in tree.body if not (name==\'autonomy_model.py\' and isinstance(n,ast.FunctionDef) and n.name==\'inspection_identifier\')]\n if name==\'dispatch_recovery.py\':\n  marker=ast.dump(ast.parse("CONTROLLER_VERSION=\'controller-terminal-reconciliation-v1\'").body[0],include_attributes=False)\n  tree.body=[n for n in tree.body if not (isinstance(n,ast.FunctionDef) and n.name==\'controller_terminal\')\n   and ast.dump(n,include_attributes=False)!=marker]\n return ast.dump(tree,include_attributes=False)\ndef inspect(before,after,manifest):\n before=Path(before);after=Path(after);files={};changed=[]\n if not isinstance(manifest.get(\'files\'),dict) or not manifest[\'files\']:raise ScopeError(\'Bound source manifest required.\')\n for name,expected in manifest[\'files\'].items():\n  old=load(before/name);new=load(after/name)\n  if digest(old)!=expected:raise ScopeError(\'Previous source binding differs: \'+name)\n  files[name]=digest(new)\n  if name==APPROVAL:continue\n  if old==new:continue\n  if name not in set(FUNCTIONS)|{\'store.py\'}:raise ScopeError(\'Dedicated scientific or transport qualification required: \'+name)\n  if protected_tree(old,name)!=protected_tree(new,name):raise ScopeError(\'Frozen scientific/request/module content changed: \'+name)\n  changed.append(name)\n if not changed:raise ScopeError(\'No operational runtime source change requires release.\')\n for path in before.parent.joinpath(\'research_workflow/tests\').rglob(\'*\'):\n  if path.is_file() and not path.is_symlink() and path.suffix==\'.py\':\n   relative=path.relative_to(before.parent)\n   if load(path)!=load(after.parent/relative):raise ScopeError(\'Existing research validation changed: \'+str(relative))\n existing={p.name for p in before.iterdir() if p.is_file() and p.suffix in (\'.py\',\'.mjs\')}\n added={p.name for p in after.iterdir() if p.is_file() and p.suffix in (\'.py\',\'.mjs\')}-existing\n if added:raise ScopeError(\'Additional executable modules need a declared qualification: \'+\',\'.join(sorted(added)))\n inherited=json.loads(load(before/APPROVAL))\n previous_binding=digest(canonical({n:v for n,v in manifest[\'files\'].items() if n!=APPROVAL}))\n if not inherited.get(\'approved_for_lab_recovery\') or inherited.get(\'qualified_files_sha256\')!=previous_binding:raise ScopeError(\'Previous model/runtime qualification does not bind its source.\')\n return dict(version=VERSION,changed_files=sorted(changed),files=files,\n  source_binding=digest(canonical({n:v for n,v in files.items() if n!=APPROVAL})),\n  previous_source_revision=manifest[\'source_revision\'],previous_source_binding=previous_binding,\n  inherited_model_evidence_sha256=inherited[\'evidence_sha256\'],scientific_and_request_content_unchanged=True,\n  existing_tests_unchanged=True,new_provider_calls=0)\ndef approval(scope,certificate_sha256):\n if scope.get(\'version\')!=VERSION or scope.get(\'scientific_and_request_content_unchanged\') is not True or scope.get(\'existing_tests_unchanged\') is not True:raise ScopeError(\'Qualified operational scope required.\')\n if len(certificate_sha256)!=64 or any(c not in \'0123456789abcdef\' for c in certificate_sha256):raise ScopeError(\'Immutable qualification artifact hash required.\')\n return dict(version=\'autonomous-repair-release-v1\',approved_for_lab_recovery=True,qualified_files_sha256=scope[\'source_binding\'],evidence_sha256=certificate_sha256)\ndef pin(previous,revision,files):\n if len(revision)!=40 or any(c not in \'0123456789abcdef\' for c in revision) or set(files)!=set(previous[\'files\']):raise ScopeError(\'Exact reviewed runtime source and unchanged file coverage required.\')\n return dict(version=previous[\'version\']+1,source_revision=revision,files=dict(sorted(files.items())))\n'


class RuntimeScopeRefusalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'candidate'
        (self.directory / 'research_workflow').mkdir(parents=True)
        self.controller = Mock()
        self.controller.state = {'original_call': 'unchanged', 'changes': []}
        self.controller.store.spool = self.root
        self.controller.store.task = {'id': 'original-task', 'lease_token': 'original-lease'}
        self.controller.github.main.return_value = 'a' * 40
        self.controller.flags = {'deadline_at': '2099-01-01T00:00:00Z'}
        self.controller.image = 'frozen-image'
        qualification = self.root / 'original-task/runtime-qualification'
        self.infra = qualification / ('a' * 40) / 'infra'
        self.infra.mkdir(parents=True)
        self.previous = qualification / ('b' * 40) / 'backend'
        (self.previous / 'research_workflow').mkdir(parents=True)
        old = b'def request():\n return 1\n'
        (self.previous / 'research_workflow/autonomy_model.py').write_bytes(old)
        (self.directory / 'research_workflow/autonomy_model.py').write_bytes(old.replace(b'1', b'2'))
        self.manifest = {'source_revision': 'b' * 40, 'files': {'autonomy_model.py': digest(old)}}
        (self.infra / 'research-workflow-runtime.json').write_bytes(canonical(self.manifest))
        (self.root / 'research-runtime-checks.py').write_text('# frozen test fixture\n')
        self.assertEqual(digest(FROZEN_GATE_SOURCE), FROZEN_GATE_SHA256)
        self.gate = types.ModuleType('regression_research_runtime_gate')
        exec(compile(FROZEN_GATE_SOURCE, '<frozen research-runtime gate fixture>', 'exec'),
             self.gate.__dict__)
        self.original_state = copy.deepcopy(self.controller.state)
        self.original_task = copy.deepcopy(self.controller.store.task)
        self.candidate_bytes = (self.directory / 'research_workflow/autonomy_model.py').read_bytes()
        self.candidate_patch = {'version': 'platform-patch-v1', 'files': [], 'tree_sha256': 'c' * 64}
        self.original_patch = copy.deepcopy(self.candidate_patch)

    def qualify(self):
        return research_runtime.qualify(self.controller, self.directory, self.candidate_patch,
                                        {'passed': True}, {'id': 'original-check', 'sha256': 'd' * 64})

    def assert_no_admission(self):
        self.assertEqual(self.controller.state, self.original_state)
        self.assertEqual(self.controller.store.task, self.original_task)
        self.assertEqual(self.candidate_patch, self.original_patch)
        self.assertEqual((self.directory / 'research_workflow/autonomy_model.py').read_bytes(), self.candidate_bytes)
        self.controller.store.event.assert_not_called()
        self.controller.store.rpc.assert_not_called()
        self.controller.operation.assert_not_called()
        self.controller.save_state.assert_not_called()
        self.controller.github.archive.assert_not_called()
        self.assertFalse((self.directory / research_runtime.APPROVAL).exists())

    def test_fixture_does_not_depend_on_sibling_repository(self):
        # Backend checks receive no sibling infra repository or research gate.
        with patch.object(research_runtime, 'gate', side_effect=AssertionError('external gate access')):
            self.setUp()
        self.assertTrue(issubclass(self.gate.ScopeError, ValueError))

    def test_real_frozen_request_scope_rejection_blocks_before_execution(self):
        # Reproduce the recorded worker.run -> research_runtime.qualify failure
        # using the unchanged gate, not an invented successful qualification.
        with patch.object(research_runtime, 'gate', return_value=self.gate), \
                patch.object(research_runtime.subprocess, 'run') as executed:
            with self.assertRaises(Hold) as caught:
                self.qualify()
        self.assertEqual(caught.exception.status, 'blocked')
        self.assertIn('Frozen scientific/request/module content changed: autonomy_model.py', str(caught.exception))
        self.assertIn('dedicated qualified runtime adapter', str(caught.exception))
        executed.assert_not_called()
        self.controller.boundary.assert_not_called()
        self.controller.store.artifact.assert_not_called()
        self.assert_no_admission()

    def test_recheck_scope_rejection_retains_output_without_approval(self):
        gate = Mock(ScopeError=self.gate.ScopeError)
        gate.inspect.side_effect = [{'source_binding': 'e' * 64}, self.gate.ScopeError('Source binding differs')]
        result = subprocess.CompletedProcess([], 0, b'fixture output', b'')
        self.controller.store.artifact.return_value = {'id': 'retained-output', 'sha256': 'f' * 64}
        with patch.object(research_runtime, 'gate', return_value=gate), \
                patch.object(research_runtime, 'ROOT', self.root), \
                patch.object(research_runtime.subprocess, 'run', return_value=result) as executed:
            with self.assertRaises(Hold) as caught:
                self.qualify()
        self.assertEqual(caught.exception.status, 'blocked')
        self.assertIn('Research runtime recheck refused: Source binding differs', str(caught.exception))
        self.assertEqual(executed.call_count, 1)
        self.assertIn('none', executed.call_args.args[0])
        self.assertEqual(gate.inspect.call_count, 2)
        self.controller.store.artifact.assert_called_once()
        name, raw = self.controller.store.artifact.call_args.args
        self.assertEqual(name, 'research-runtime-frozen-checks.json')
        self.assertEqual(json.loads(raw)['stdout'], 'fixture output')
        self.assert_no_admission()

    def test_unexpected_gate_fault_remains_an_unexpected_fault(self):
        gate = Mock(ScopeError=self.gate.ScopeError)
        gate.inspect.side_effect = RuntimeError('Unexpected gate defect')
        with patch.object(research_runtime, 'gate', return_value=gate), \
                patch.object(research_runtime.subprocess, 'run') as executed:
            with self.assertRaisesRegex(RuntimeError, 'Unexpected gate defect'):
                self.qualify()
        executed.assert_not_called()
        self.assert_no_admission()

    def test_recheck_scope_drift_still_fails_closed(self):
        gate = Mock(ScopeError=self.gate.ScopeError)
        gate.inspect.side_effect = [{'source_binding': 'e' * 64}, {'source_binding': 'f' * 64}]
        self.controller.store.artifact.return_value = {'id': 'retained-output', 'sha256': 'f' * 64}
        with patch.object(research_runtime, 'gate', return_value=gate), \
                patch.object(research_runtime, 'ROOT', self.root), \
                patch.object(research_runtime.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
            with self.assertRaisesRegex(Hold, 'Runtime source changed during qualification'):
                self.qualify()
        self.assert_no_admission()


if __name__ == '__main__':
    unittest.main()
