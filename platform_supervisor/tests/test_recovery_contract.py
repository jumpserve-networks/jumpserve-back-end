"""Private recovery schema delivery and diagnosis originals, never authority."""
import copy,json,tempfile,unittest,uuid
from pathlib import Path
from platform_supervisor import repair
from platform_supervisor.common import canonical,digest,Hold
from platform_supervisor.tests.test_repair_handoff import Store


def contract():
    definition='CREATE FUNCTION public.research_model_reconcile_queued(uuid,text) RETURNS jsonb AS $$ SELECT null::jsonb $$ LANGUAGE sql;\n'
    columns=['dispatch_id','study_id','task_id','request_sha256','snapshot_raw','snapshot_sha256','parent_turn_status','proof','created_at']
    return dict(version=repair.CONTRACT_VERSION,queued_admission=dict(version='queued-dispatch-reconciliation-v1',
        read=dict(relation='research_queued_dispatch_reconciliations',columns=columns,actual_columns=columns,order='created_at.asc,dispatch_id.asc',access='service-only'),
        reconcile=dict(signature='public.research_model_reconcile_queued(uuid,text)',definition=definition,sha256=digest(definition.encode())),
        permissions=dict(service_select=True,browser_select=False,service_mutation=False,browser_reconcile=False)))


class RecoveryContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store(self.root)
        self.store.packet.update(recovery_contract=contract(),recovery_contract_sha256='b'*64)

    def test_current_schema_and_kind_tagged_diagnosis_are_hash_verified_and_delivered(self):
        packet=self.store.packet;raw=canonical(dict(waiting=False,validated=False,reconciled_dispatches=[dict(kind='atomic-unclaimed-dispatch')]))
        sha=digest(raw);path=packet['assessment_id']+'/'+sha
        packet['artifacts']=[dict(id=str(uuid.uuid4()),sha256=sha,byte_count=len(raw),storage_path=path,provenance=dict(kind='autonomous-repair-diagnosis'))]
        self.store.research={'/storage/v1/object/research-workflow-raw/'+path:raw};state={}
        repair.deliver(self.store,self.root,state)
        summary=json.loads((self.root/'references/assessment-repair/summary.json').read_bytes())
        view=(self.root/'references/assessment-repair/recovery-contract.json').read_bytes()
        self.assertEqual(digest(view),summary['recovery_contract']['view_sha256'])
        self.assertIn(b'created_at.asc,dispatch_id.asc',view)
        self.assertIn(b'atomic-unclaimed-dispatch',(self.root/'references/assessment-repair/artifacts'/(sha+'.json')).read_bytes())
        self.assertEqual(state['repair_contract']['version'],repair.CONTRACT_VERSION)
        self.assertFalse(json.loads(raw)['validated'])
        self.assertEqual(self.store.artifacts['assessment-repair-context.json'][1],canonical(packet))

    def test_mismatched_function_hash_schema_or_browser_access_fences_model_delivery(self):
        original=copy.deepcopy(self.store.packet)
        for change in ('hash','order','browser'):
            self.store.packet=copy.deepcopy(original);q=self.store.packet['recovery_contract']['queued_admission']
            if change=='hash':q['reconcile']['definition']+='changed'
            if change=='order':q['read']['order']='created_at.asc,id.asc'
            if change=='browser':q['permissions']['browser_select']=True
            with self.assertRaisesRegex(Hold,'recovery contract'):repair.deliver(self.store,self.root,{})

    def test_missing_diagnosis_is_not_manufactured_and_contract_cannot_silently_disappear(self):
        state={};repair.deliver(self.store,self.root,state)
        summary=json.loads((self.root/'references/assessment-repair/summary.json').read_bytes())
        self.assertEqual(summary['artifacts'],[])
        self.store.packet.pop('recovery_contract')
        with self.assertRaisesRegex(Hold,'unavailable'):repair.deliver(self.store,self.root,state)


if __name__=='__main__':unittest.main()
