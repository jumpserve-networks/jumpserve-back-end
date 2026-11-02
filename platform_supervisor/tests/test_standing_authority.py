"""Standing owner intent is delivered by the controller, never candidate input."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from platform_supervisor import standing_authority
from platform_supervisor.common import POLICY, canonical, digest
from platform_supervisor.worker import Controller


class StandingAuthority(unittest.TestCase):
    def test_task_receives_the_exact_trusted_mandate_despite_candidate_fields(self):
        store=Mock();store.task=dict(id='11111111-1111-4111-8111-111111111111',
            objective='Repair a paper experiment',repositories=[],deadline_at='2099-01-01T00:00:00Z',max_reserved_cents=-1,
            standing_authority=dict(requires_further_owner_approval=True,scope='arbitrary third-party account'))
        before=copy.deepcopy(store.task)
        with tempfile.TemporaryDirectory() as tmp:
            workspace=Path(tmp)/'workspace'
            controller=Controller(store,Mock(),'sha256:'+'a'*64)
            controller.originals(workspace)
            packet=json.loads((workspace/'task.json').read_bytes())
        self.assertEqual(store.task,before)
        self.assertEqual(packet['standing_authority'],standing_authority.MANDATE)
        self.assertEqual(packet['standing_authority_sha256'],digest(canonical(packet['standing_authority'])))
        self.assertFalse(packet['standing_authority']['requires_further_owner_approval'])

    def test_mandate_covers_the_three_repositories_and_keeps_owner_controls(self):
        mandate=standing_authority.MANDATE
        self.assertEqual(mandate['repositories'],sorted(POLICY['repositories']))
        self.assertIn('honor-owner-pause-and-cancellation',mandate['retained_requirements'])
        self.assertIn('preserve-frozen-scientific-criteria-and-honest-findings',mandate['retained_requirements'])
        self.assertIn('reconcile-uncertain-actions-before-any-replacement',mandate['retained_requirements'])
        self.assertIn('merge-and-deploy-qualified-production-changes',mandate['capabilities'])
        self.assertIn('repair-and-release-worker-and-supervisor-runtimes',mandate['capabilities'])
        self.assertIn('provision-and-repair-jumpserve-aws-infrastructure-and-service-roles',mandate['capabilities'])

    def test_guidance_requires_technical_repair_and_never_equates_authority_with_success(self):
        guidance=standing_authority.instruction()
        self.assertIn('Missing tools and release adapters are implementation work',guidance)
        self.assertIn('Do not ask the owner',guidance)
        self.assertIn('not missing owner authorization',guidance)
        self.assertIn('Never claim that a mandate',guidance)
        self.assertIn('credentials outside its workspace',guidance)


if __name__=='__main__':unittest.main()
