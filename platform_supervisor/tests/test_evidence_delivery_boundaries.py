"""Passive evidence copies preserve original task scope, hashes, and blockers."""
import io
import json
import base64
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
from platform_supervisor import repair,working_evidence as working,incident_evidence as incident
from platform_supervisor.common import Hold,canonical,digest
from platform_supervisor.tests import test_repair_handoff as repair_fixtures,test_working_evidence as working_fixtures


def archive(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name,raw in files.items():bundle.writestr(name,raw)
    return output.getvalue()


class EvidenceDeliveryBoundaries(unittest.TestCase):
    def fixture(self,module,name):
        case=getattr(module,name)();case.setUp();self.addCleanup(case.doCleanups);return case

    def test_working_delivery_contract_drift_stops_before_retaining_another_context(self):
        for declared in ['foreign',None]:
            case=self.fixture(repair_fixtures,'RepairHandoff');state={}
            if declared is None:state['working_evidence_contract']=working.VERSION
            else:case.store.packet['working_checkpoint_delivery']=declared
            with self.assertRaisesRegex(Hold,'Working checkpoint|working checkpoint'):repair.deliver(case.store,case.root,state)
            self.assertEqual(case.store.artifacts,{})

    def test_unknown_diagnostics_are_omitted_and_malformed_original_hashes_cannot_be_downloaded(self):
        for defect in ['unknown','hash']:
            case=self.fixture(repair_fixtures,'RepairHandoff');case.store.packet['artifacts']=[dict(id='original',sha256='invalid',byte_count=1,provenance=dict(filename='unrelated-private-file' if defect=='unknown' else 'stdout.bin'))]
            if defect=='unknown':
                repair.deliver(case.store,case.root,{});summary=json.loads((case.root/'references/assessment-repair/summary.json').read_bytes());self.assertEqual(summary['artifacts'],[])
            else:
                with self.assertRaisesRegex(Hold,'artifact identity'):repair.deliver(case.store,case.root,{})

    def test_unchanged_unblocked_context_keeps_its_guard_and_matching_prior_blocker_copy(self):
        case=self.fixture(repair_fixtures,'RepairHandoff');state={};repair.deliver(case.store,case.root,state);state['repair_guard']['original_marker']='unchanged';repair.deliver(case.store,case.root,state);self.assertEqual(state['repair_guard']['original_marker'],'unchanged')
        original=canonical(dict(version='platform-progress-v1',ready=False,status='blocked',remaining_blocker='Original blocker'));(case.root/'progress.json').write_bytes(original);target=case.root/'previous-candidates'/('blocked-progress-'+digest(original)+'.json');target.parent.mkdir();target.write_bytes(original);case.store.packet['evidence_revision']='b'*64;repair.deliver(case.store,case.root,state);self.assertEqual(target.read_bytes(),original);self.assertFalse((case.root/'progress.json').exists())

    def test_missing_unknown_or_nonfile_refresh_reports_never_select_an_original_checkpoint(self):
        for defect in ['missing','version','directory']:
            case=self.fixture(working_fixtures,'WorkingEvidence');report=case.root/'rebase-conflicts.json'
            if defect=='missing':report.unlink()
            elif defect=='version':report.write_bytes(canonical(dict(version='unknown')))
            else:report.unlink();report.mkdir()
            with self.subTest(defect=defect):
                if defect=='directory':
                    with self.assertRaisesRegex(Hold,'bounded plain evidence'):working.deliver(case.store,case.root,case.controller.state)
                else:working.deliver(case.store,case.root,case.controller.state)
            self.assertEqual(case.store.requests,[])

    def test_refresh_report_plan_limit_is_explicit_and_shared_checkpoint_bytes_are_downloaded_once(self):
        case=self.fixture(working_fixtures,'WorkingEvidence');folder=case.root/'previous-candidates';folder.mkdir()
        for index in range(working.MAX_PLANS+1):
            plan=dict(case.plan,original_note='Distinct retained source refresh '+str(index));reference=case.store.add('repair-source-refresh-plan.json',canonical(plan));report=dict(case.report,plan_sha256=reference['sha256']);(folder/f'source-refresh-{index:02}.json').write_bytes(canonical(report))
        working.deliver(case.store,case.root,case.controller.state);result=json.loads((case.root/'references/working-checkpoints/index.json').read_bytes());self.assertFalse(result['complete']);self.assertEqual(result['omitted'],2);self.assertEqual(len(result['checkpoints']),working.MAX_PLANS);self.assertEqual(sum(path.endswith(case.pointer['path']) for path in case.store.requests),1)

    def test_total_working_evidence_limit_applies_to_distinct_valid_original_archives(self):
        case=self.fixture(working_fixtures,'WorkingEvidence');folder=case.root/'previous-candidates';folder.mkdir()
        for index in range(7):
            raw=working_fixtures.source.checkpoint({working_fixtures.REPO:dict(files=[dict(path='original.bin',mode='100644',content=base64.b64encode(os.urandom(2900000)).decode())])},{})
            checkpoint=case.store.add('working-checkpoint.zip',raw);plan=dict(case.plan,original_working_artifact=checkpoint);reference=case.store.add('repair-source-refresh-plan.json',canonical(plan));report=dict(case.report,plan_sha256=reference['sha256'],original_working_artifact=checkpoint);(folder/f'source-refresh-{index}.json').write_bytes(canonical(report))
        with self.assertRaisesRegex(Hold,'total delivery bound'):working.deliver(case.store,case.root,case.controller.state)

    def test_legacy_native_history_is_supported_but_oversized_or_incomplete_members_stay_unavailable(self):
        self.assertIsNotNone(incident.checkpoint_view(archive({'native-tool-history.json':canonical([])})))
        with self.assertRaisesRegex(Hold,'member exceeds'):incident.checkpoint_view(archive({'work-packet.json':b'x'*1000001}))
        raw=archive({'work-packet.json':b'{}'})
        with patch.object(zipfile.ZipFile,'open',return_value=io.BytesIO(b'')),self.assertRaisesRegex(Hold,'member size differs'):incident.checkpoint_view(raw)

    def test_unrecognized_diagnostics_and_invalid_recovery_contracts_never_authorize_repair(self):
        self.assertIsNone(repair._artifact_view(b'Original private payload','unrecognized-private-file'))
        self.assertEqual(repair._artifact_view(b'Original plain log','stdout.bin'),{'text':'Original plain log'})
        with self.assertRaisesRegex(Hold,'recovery contract is incomplete'):repair.recovery_contract({'recovery_contract':{}})
        with self.assertRaisesRegex(Hold,'history must be an array'):incident.history_view(canonical({'original':'not a history array'}))
        with self.assertRaisesRegex(Hold,'unsupported record'):incident.history_view(canonical([{'type':'unrecognized','call_id':'original'}]))
        for files in [{str(i):b'' for i in range(129)},{'project/experiment.py':b'x'*4000001},{'native-tool-history.json':canonical({'version':'foreign'})}]:
            with self.subTest(files=list(files)[:2]),self.assertRaises(Hold):incident.checkpoint_view(archive(files))

    def test_completed_controller_stream_projection_records_validity_without_executing_tool_content(self):
        import siwc_protocol as wire
        terminal={'status':'completed','model':wire.MODEL,'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Original retained decision'}]}]};raw=b'data: '+canonical({'type':'response.completed','response':terminal})+b'\n\n'
        with patch.object(wire,'structured_response',return_value={'validated':True}) as validate:value=incident.controller_response_view(raw)
        self.assertTrue(value['decision_valid']);validate.assert_called_once_with(terminal)

    def test_oversized_diagnostics_remain_explicitly_unavailable_without_downloading_or_truncating_originals(self):
        case=self.fixture(repair_fixtures,'RepairHandoff');case.store.packet['artifacts']=[dict(id='oversized',sha256='a'*64,byte_count=128001,provenance={'filename':'stdout.bin'})];repair.deliver(case.store,case.root,{})
        summary=json.loads((case.root/'references/assessment-repair/summary.json').read_bytes());self.assertFalse(summary['artifacts'][0]['available']);self.assertEqual(summary['artifacts'][0]['original_sha256'],'a'*64)

    def test_invalid_semantic_baselines_and_conflicting_blocker_copies_cannot_reset_retained_progress(self):
        case=self.fixture(repair_fixtures,'RepairHandoff')
        with self.assertRaisesRegex(Hold,'semantic evidence baseline differs'):repair.deliver(case.store,case.root,{'repair_guard':{'semantic_baseline':{}}})
        raw=canonical({'version':'platform-progress-v1','status':'blocked','ready':False,'remaining_blocker':'Original missing prerequisite'});progress=case.root/'progress.json';progress.write_bytes(raw);target=case.root/'previous-candidates'/('blocked-progress-'+digest(raw)+'.json');target.parent.mkdir();target.write_bytes(b'Conflicting retained original')
        with self.assertRaisesRegex(Hold,'Retained blocked progress differs'):repair.deliver(case.store,case.root,{})
        self.assertEqual(progress.read_bytes(),raw);self.assertEqual(target.read_bytes(),b'Conflicting retained original')

    def test_working_evidence_rejects_foreign_task_pointers_and_linked_destinations(self):
        case=self.fixture(working_fixtures,'WorkingEvidence')
        with self.assertRaisesRegex(Hold,'outside this task'):working.pointer({'sha256':'a'*64,'path':'foreign/'+'a'*64},case.store.task['id'])
        linked=case.root/'linked';linked.symlink_to(case.root/'original')
        with self.assertRaisesRegex(Hold,'workspace link'):working.write_original(linked,b'Original bytes',case.root)
        with self.assertRaisesRegex(Hold,'unsafe members'):working.members(archive({'work-packet.json':b'{}','../escaped.json':b'{}'}),case.store.task['repositories'])
        with self.assertRaisesRegex(Hold,'expansion exceeds'):working.members(archive({str(index):b'' for index in range(5)}),case.store.task['repositories'])
        case.report['plan_sha256']='not-a-sha256';(case.root/'rebase-conflicts.json').write_bytes(canonical(case.report))
        with self.assertRaisesRegex(Hold,'plan identity is invalid'):working.deliver(case.store,case.root,case.controller.state)
        (case.root/'rebase-conflicts.json').write_bytes(b'Original invalid report JSON');self.assertIsNone(working.deliver(case.store,case.root,case.controller.state))

    def test_retained_incident_continuation_is_linked_in_the_repair_summary_without_changing_original_context(self):
        from platform_supervisor import continuations
        case=self.fixture(repair_fixtures,'RepairHandoff');original=canonical(case.store.packet)
        with patch.object(continuations,'deliver',return_value={'original_continuation':'retained'}):repair.deliver(case.store,case.root,{})
        summary=json.loads((case.root/'references/assessment-repair/summary.json').read_bytes());self.assertEqual(summary['incident_continuation'],'references/incident-continuation/index.json');self.assertEqual(canonical(case.store.packet),original)

    def test_historical_working_plans_are_omitted_and_new_deliveries_cannot_change_seen_original_identity(self):
        case=self.fixture(working_fixtures,'WorkingEvidence');case.controller.state['working_evidence_seen']={case.plan_pointer['sha256']:'0'*64}
        with self.assertRaisesRegex(Hold,'checkpoint identity changed'):working.deliver(case.store,case.root,case.controller.state)
        case.controller.state['working_evidence_seen']={str(index):'a'*64 for index in range(512)}
        with self.assertRaisesRegex(Hold,'index exceeds'):working.deliver(case.store,case.root,case.controller.state)
        case.controller.state.pop('working_evidence_seen');case.plan['next']['revision']='c'*40;pointer=case.store.add('repair-source-refresh-plan.json',canonical(case.plan));case.report['plan_sha256']=pointer['sha256'];(case.root/'rebase-conflicts.json').write_bytes(canonical(case.report));self.assertIsNone(working.deliver(case.store,case.root,case.controller.state));index=json.loads((case.root/'references/working-checkpoints/index.json').read_bytes());self.assertFalse(index['complete']);self.assertEqual(index['omitted'],1)


if __name__=='__main__':unittest.main()
