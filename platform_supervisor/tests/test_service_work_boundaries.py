"""Service handoff guards retain original stops, candidates, and immutable limits."""
import copy
import unittest
from unittest.mock import patch
from platform_supervisor import service_work as service
from platform_supervisor.common import Hold,canonical,identity
from platform_supervisor.tests import test_service_work as fixtures


class ServiceWorkBoundaries(unittest.TestCase):
    def fixture(self):
        case=fixtures.ServiceWork();case.setUp();self.addCleanup(case.doCleanups);return case,case.controller()

    def test_invalid_progress_is_not_a_service_request_and_linked_original_stops_cannot_be_deleted(self):
        case,controller=self.fixture();path=case.root/'progress.json';path.write_bytes(b'Original invalid JSON')
        self.assertFalse(service.capture(case.root,{}));path.unlink();target=case.root/'original.json';target.write_bytes(b'Original stop');path.symlink_to(target)
        with self.assertRaisesRegex(Hold,'reconciliation'):service.restore(controller,case.root,{'request_id':case.request['id']})
        self.assertEqual(target.read_bytes(),b'Original stop')

    def test_service_context_delivers_only_the_original_implementation_and_does_not_repeat_feedback(self):
        case,controller=self.fixture();packet=dict(version=service.VERSION,evidence=None,implementation=None,activation=None);controller.store.request.return_value=packet
        service.deliver(controller,case.root);feedback=controller.state['feedback'];self.assertIn('protected, source-bound release',feedback);service.deliver(controller,case.root);self.assertEqual(controller.state['feedback'],feedback)
        packet['implementation']=case.request
        with patch.object(service,'prepare') as prepare:service.deliver(controller,case.root)
        prepare.assert_called_once_with(controller,case.root,case.request)

    def test_unsettled_original_candidate_must_match_before_any_superseding_candidate_can_be_prepared(self):
        case,controller=self.fixture();legacy=identity(controller.store.task['id'],'service-manifest:'+case.request['id']);candidate=dict(id=legacy,base_sha='a'*40,tree_sha256='original-tree',patch_artifact_id='original-patch',checks_artifact_id='original-checks');controller.state['changes']=[candidate];original=dict(candidate,status='tested');controller.store.rows.return_value=[original]
        before=copy.deepcopy(controller.state);service.recover_superseded_candidate(controller,case.request);self.assertEqual(controller.state,before)
        original['tree_sha256']='different'
        with self.assertRaisesRegex(Hold,'differs from an unsettled original'):service.recover_superseded_candidate(controller,case.request)
        controller.store.artifact.assert_not_called()

    def test_superseded_candidate_requires_a_fresh_base_even_when_no_external_operation_is_pending(self):
        case,controller=self.fixture();ident=service.candidate_id(controller,case.request,'a'*40);candidate=dict(id=ident,base_sha='a'*40,status='superseded');controller.state['changes']=[candidate];controller.store.rows.side_effect=lambda name,**kwargs:[dict(candidate)] if name=='changes' else []
        with self.assertRaisesRegex(Hold,'fresh retained base'):service.recover_superseded_candidate(controller,case.request)
        controller.store.artifact.assert_not_called()


if __name__=='__main__':unittest.main()
