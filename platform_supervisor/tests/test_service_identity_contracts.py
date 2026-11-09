"""Unconfirmed service evidence never authorizes another release or replacement."""
import copy
import unittest
from platform_supervisor import service_work as service
from platform_supervisor.common import Hold,canonical,digest,identity
from platform_supervisor.tests.test_service_work import ServiceWork


class ServiceIdentityContracts(unittest.TestCase):
    def fixture(self):
        case=ServiceWork();case.setUp();self.addCleanup(case.doCleanups);return case,case.controller()

    def test_activation_without_a_prior_stop_preserves_the_original_controller_limits(self):
        case,controller=self.fixture();controller.state.update(committed_cents=100,deadline_at='original-deadline')
        service.restore(controller,case.root,dict(request_id=case.request['id']))
        self.assertEqual(controller.state['committed_cents'],100);self.assertEqual(controller.state['deadline_at'],'original-deadline');controller.store.artifact.assert_not_called()
        self.assertEqual(controller.state['service_work_restored'],case.request['id'])

    def test_unrelated_merged_or_absent_candidates_do_not_get_superseded(self):
        for defect in ['absent','foreign','merged','missing-original']:
            case,controller=self.fixture();legacy=identity(controller.store.task['id'],'service-manifest:'+case.request['id']);candidate=dict(id=legacy,base_sha='a'*40)
            if defect=='absent':controller.state['changes']=[]
            elif defect=='foreign':controller.state['changes']=[dict(candidate,id='foreign')]
            elif defect=='merged':controller.state['changes']=[dict(candidate,merge_sha='b'*40)]
            else:controller.state['changes']=[candidate];controller.store.rows.return_value=[]
            before=copy.deepcopy(controller.state)
            with self.subTest(defect=defect):
                if defect=='missing-original':
                    with self.assertRaisesRegex(Hold,'original change record'):service.recover_superseded_candidate(controller,case.request)
                else:service.recover_superseded_candidate(controller,case.request)
            self.assertEqual(controller.state,before);controller.record_change.assert_not_called()

    def test_service_manifests_require_the_owning_repository_and_unchanged_plain_path(self):
        for defect in ['repository','link','changed']:
            case,controller=self.fixture();path=case.root/'jumpserve-infra/services'/f'{case.request["id"]}.json'
            if defect=='repository':controller.store.task['repositories']=['jumpserve-networks/jumpserve-back-end']
            else:
                path.parent.mkdir()
                if defect=='link':path.symlink_to(case.root/'original-private')
                else:path.write_bytes(b'Original different service request')
            with self.subTest(defect=defect),self.assertRaises(Hold):service.prepare(controller,case.root,case.request)
            controller.record_change.assert_not_called();self.assertNotIn('tested',controller.state)

    def test_observation_inventory_and_installed_contract_must_be_complete(self):
        for records in ['not a list',[{}]*33]:
            case,controller=self.fixture()
            with self.assertRaisesRegex(Hold,'coverage exceeds'):service.runtime_observations(controller,case.root,dict(runtime_observations=records))
            controller.store.request.assert_not_called()
        for packet in [None,dict(version='unsupported')]:
            case,controller=self.fixture();controller.store.request.return_value=packet
            with self.assertRaisesRegex(Hold,'service contract differs'):service.deliver(controller,case.root)

    def test_failed_extraction_evidence_keeps_unrelated_artifacts_separate_and_requires_exact_hashes(self):
        original=canonical(dict(reason='Original parser failure'));sha=digest(original)
        for defect in ['unrelated','size','hash','bytes']:
            case,controller=self.fixture();artifact=dict(id=case.request['id'],kind='failed-text-extraction',sha256=sha,byte_count=len(original))
            if defect=='unrelated':artifact['kind']='other-original'
            elif defect=='size':artifact['byte_count']=True
            elif defect=='hash':artifact['sha256']='invalid'
            evidence=dict(assessment_id=case.request['id'],artifacts=[artifact]);packet=dict(version=service.VERSION,evidence=evidence,implementation=None,activation=None)
            controller.store.request.side_effect=lambda path,*args,**kw:(original+b'changed' if defect=='bytes' else original) if path.startswith('/storage/') else packet
            with self.subTest(defect=defect):
                if defect in ['hash','bytes']:
                    with self.assertRaises(Hold):service.deliver(controller,case.root)
                else:
                    service.deliver(controller,case.root);self.assertEqual(evidence['failed_text_extractions'],[]);self.assertNotIn('pending_service_work',controller.state)


if __name__=='__main__':unittest.main()
