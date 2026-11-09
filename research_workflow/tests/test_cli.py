"""Exercise operator commands with local originals and synthetic persistence."""
import contextlib
import copy
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli
import workflow
from test_workflow import fixture
from test_preparation import small_plan


class OperatorCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='jumpserve-cli-unit-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.study, self.actor, self.request = (str(uuid.uuid4()) for _ in range(3))
        self.counter = 0
        for name in ('rpc', 'append', 'append_bundle', 'artifact', 'rows', 'owns', 'snapshot'):
            mock = self.enterContext(patch.object(cli.store, name))
            setattr(self, name, mock)
        self.owns.return_value = True
        self.rows.return_value = []
        self.artifact.side_effect = lambda study, actor, raw, identity, media, provenance: {
            'id': identity, 'sha256': workflow.digest(raw), 'byte_count': len(raw), 'provenance': provenance}
        self.transport = self.enterContext(patch.object(cli.store, 'request', side_effect=AssertionError('Unexpected external transport')))

    def file(self, name, value):
        path = self.directory / name
        path.write_bytes(value if isinstance(value, bytes) else workflow.canonical(value))
        return str(path)

    def output(self):
        self.counter += 1
        return str(self.directory / f'output-{self.counter}.json')

    def invoke(self, command, *arguments, persist=False, output=None, scoped=True):
        output = output or self.output()
        argv = ['cli.py', command, *map(str, arguments), '--output', output]
        if scoped:
            argv += ['--study-id', self.study]
        if persist:
            argv += ['--persist', '--actor-id', self.actor]
        stdout = io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(stdout):
            cli.main()
        return json.loads(Path(output).read_bytes()), stdout.getvalue()

    def source_args(self, raw=b'%PDF-1.7\nsynthetic paper', audit=None):
        args = ['--file', self.file('paper.pdf', raw), '--citation', 'Synthetic paper, unit fixture',
                '--url', 'https://example.org/paper.pdf', '--version', 'fixture-v1', '--request-id', self.request]
        if audit is not None:
            args += ['--retrieval-record', self.file('audit.json', audit)]
        return args

    def campaign_args(self):
        raw, protocol, campaign = fixture()
        return raw, protocol, campaign, ['--input', self.file('input.json', raw),
            '--protocol', self.file('protocol.json', protocol), '--campaign', self.file('campaign.json', campaign),
            '--request-id', self.request]

    def test_output_is_exclusive_and_existing_originals_are_not_overwritten(self):
        output = self.file('retained.json', b'original bytes')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.invoke('intake', '--title', 'Fixture', '--url', 'https://example.org/paper',
                        '--domain', 'Networking', '--scope', 'Unit fixture', output=output)
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(Path(output).read_bytes(), b'original bytes')
        self.rpc.assert_not_called()
        nested = self.directory / 'nested/output.json'
        cli.save(nested, {'zero': 0})
        self.assertEqual(nested.read_bytes(), b'{"zero":0}\n')
        with self.assertRaises(FileExistsError):
            cli.save(nested, {'zero': 1})

    def test_persistence_requires_actor_before_any_write(self):
        argv = ['cli.py', 'intake', '--title', 'Fixture', '--url', 'https://example.org/paper',
                '--domain', 'Networking', '--scope', 'Unit fixture', '--study-id', self.study,
                '--output', self.output(), '--persist']
        with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main()
        self.assertEqual(error.exception.code, 2)
        self.rpc.assert_not_called()

    def test_intake_is_a_private_unreviewed_inventory_with_only_explicit_persistence(self):
        args = ('--title', 'Fixture', '--url', 'https://example.org/paper', '--domain', 'Networking', '--scope', 'Unit fixture')
        for persist in (False, True):
            value, output = self.invoke('intake', *args, persist=persist)
            self.assertEqual(value['paper']['title'], 'Fixture')
            self.assertFalse(value['persisted'])
            self.assertEqual(json.loads(output)['persisted'], persist)
            self.assertIn('no claims inferred', json.loads(output)['scientific_status'])
        self.rpc.assert_called_once_with('research_create_study', dict(p_actor=self.actor, p_id=self.study, p_document=value['paper']))

    def test_retrieval_preserves_status_and_nonretrieved_exit_code(self):
        for status in ('retrieved', 'blocked'):
            argv = ['cli.py', 'retrieve', '--url', 'https://example.org/paper', '--output-directory', str(self.directory)]
            with patch.object(sys, 'argv', argv), patch.object(cli.intake, 'retrieve', return_value={'status': status}) as retrieve, contextlib.redirect_stdout(io.StringIO()) as output:
                if status == 'retrieved':
                    cli.main()
                else:
                    with self.assertRaises(SystemExit) as error:
                        cli.main()
                    self.assertEqual(error.exception.code, 1)
                self.assertEqual(json.loads(output.getvalue())['status'], status)
                retrieve.assert_called_once_with('https://example.org/paper', str(self.directory), 'application/pdf')

    def test_text_packet_delegates_exact_input_and_output_without_persistence(self):
        with patch.object(cli.intake, 'text_packet', return_value={'pages': 2}) as packet:
            destination = self.output()
            with patch.object(sys, 'argv', ['cli.py', 'text-packet', '--file', 'paper.pdf', '--output', destination]), contextlib.redirect_stdout(io.StringIO()) as output:
                cli.main()
            self.assertEqual(json.loads(output.getvalue()), {'pages': 2})
            packet.assert_called_once_with('paper.pdf', destination)
        self.rpc.assert_not_called()

    def test_template_binds_exact_input_hash_and_configuration_without_running_a_campaign(self):
        raw, protocol, _ = fixture()
        configurations = protocol['document']['configurations']
        value, _ = self.invoke('template', '--input', self.file('input.json', raw), '--metric', 'latency',
            '--units', 'ms', '--tolerance', '0.1', '--configurations', self.file('configurations.json', configurations), scoped=False)
        self.assertEqual(value['input_versions'][0]['sha256'], workflow.digest(raw))
        self.assertEqual(value['configurations'], configurations)
        self.append_bundle.assert_not_called()

    def test_freeze_creates_once_and_replays_the_retained_timestamp(self):
        _, protocol, _ = fixture()
        args = ('--document', self.file('document.json', protocol['document']), '--request-id', self.request)
        preview, _ = self.invoke('freeze', *args)
        self.append.assert_not_called()
        self.assertEqual(preview['sha256'], protocol['sha256'])
        created, _ = self.invoke('freeze', *args, persist=True)
        self.append.assert_called_once_with(self.study, self.actor, 'protocols', created)
        self.rows.return_value = [created]
        retained, _ = self.invoke('freeze', *args, persist=True)
        self.assertEqual(retained, created)
        self.assertEqual(self.append.call_count, 1)
        for field, changed in [('document', {}), ('supersedes_id', str(uuid.uuid4())), ('version', 2), ('amendment_reason', 'changed')]:
            self.rows.return_value = [dict(created, **{field: changed})]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'preserve the original'):
                self.invoke('freeze', *args, persist=True)

    def test_freeze_and_source_reject_nonowners_before_writes(self):
        self.owns.return_value = False
        _, protocol, _ = fixture()
        for command, args in [('freeze', ['--document', self.file('doc.json', protocol['document']), '--request-id', self.request]),
                              ('source', self.source_args())]:
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'ownership mismatch'):
                self.invoke(command, *args, persist=True)
        self.append.assert_not_called()
        self.artifact.assert_not_called()

    def test_export_distinguishes_public_access_and_verified_owner_access(self):
        self.snapshot.return_value = {'coverage': {'records': 1}, 'records': [{'value': 0}]}
        value, output = self.invoke('export')
        self.assertEqual(value['records'][0]['value'], 0)
        self.assertEqual(json.loads(output)['coverage'], {'records': 1})
        self.snapshot.assert_called_once_with(self.study, owner=False, maximum=100000, byte_budget=128_000_000)
        value, _ = self.invoke('export', '--owner', '--actor-id', self.actor)
        self.assertEqual(value['coverage'], {'records': 1})
        for args in [('--owner',), ('--owner', '--actor-id', self.actor)]:
            self.owns.return_value = False
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'verified ownership'):
                self.invoke('export', *args)
        self.snapshot.return_value = None
        with self.assertRaisesRegex(ValueError, 'No accessible study'):
            self.invoke('export')

    def test_campaign_import_keeps_originals_and_never_claims_validation(self):
        prepared = dict(original_sha256='a' * 64, records=[{'kind': 'sources'}], aggregate_bytes=17)
        originals = {'original': b'original bytes'}
        with patch.object(cli.importer, 'prepare', return_value=(prepared, b'bundle bytes', originals)) as prepare, patch.object(cli.importer, 'persist') as persist:
            for should_persist in (False, True):
                value, output = self.invoke('import-campaign', '--bundle', 'bundle.json', '--request-id', self.request, persist=should_persist)
                self.assertEqual(value, prepared)
                self.assertEqual(json.loads(output)['scientific_validation'], 'Not established by import')
            prepare.assert_called_with('bundle.json', self.request)
            persist.assert_called_once_with(prepared, b'bundle bytes', originals, self.study, self.actor)

    def test_ipv6_import_preserves_gap_count_and_explicit_write_scope(self):
        original = dict(paper={'title': 'Fixture'}, records=[{'kind': 'gaps'}, {'kind': 'sources'}], provenance={'fixture': True})
        with patch.object(cli.bridge, 'ipv6', return_value=original) as imported:
            for persist in (False, True):
                value, output = self.invoke('import-ipv6', '--assessment', 'assessment.json', persist=persist)
                self.assertEqual(value, original)
                self.assertEqual(json.loads(output)['gaps'], 1)
            imported.assert_called_with('assessment.json', self.study)
        self.append_bundle.assert_called_once_with(self.study, self.actor, original['records'])

    def test_source_inventory_retains_exact_originals_without_asserting_substantive_review(self):
        for raw in (b'%PDF-1.7\nsynthetic paper', b'plain source bytes'):
            self.artifact.reset_mock()
            value, output = self.invoke('source', *self.source_args(raw), persist=True)
            self.assertEqual(value['record']['sha256'], workflow.digest(raw))
            self.assertEqual(value['record']['review_status'], 'retrieved-unreviewed')
            self.assertIsNone(value['record']['retrieved_url'])
            self.assertEqual(json.loads(output)['review_status'], 'retrieved-unreviewed')
            args = self.artifact.call_args.args
            self.assertEqual(args[2], raw)
            self.assertEqual(args[4], 'application/pdf' if raw.startswith(b'%PDF') else 'application/octet-stream')
            self.assertEqual(args[5]['transformation'], 'none')
        self.assertEqual(self.append_bundle.call_count, 2)
        self.artifact.reset_mock()
        self.invoke('source', *self.source_args(), persist=False)
        self.artifact.assert_not_called()

    def test_source_retrieval_audit_must_identify_the_same_bytes_url_and_attempts(self):
        raw = b'%PDF-1.7\nsynthetic paper'
        audit = dict(status='retrieved', requested_url='https://example.org/paper.pdf', retrieved_url='https://example.org/redirected.pdf',
                     sha256=workflow.digest(raw), bytes=len(raw), attempts=[{'url': 'https://example.org/paper.pdf', 'status': 'retrieved'}])
        value, _ = self.invoke('source', *self.source_args(raw, audit), persist=True)
        self.assertEqual(value['record']['retrieved_url'], audit['retrieved_url'])
        self.assertEqual(value['provenance']['retrieval_audit_sha256'], workflow.digest(workflow.canonical(audit)))
        self.assertEqual(self.artifact.call_count, 2)
        self.assertEqual(self.artifact.call_args.args[2], workflow.canonical(audit))
        for field, changed in [('status', 'failed'), ('requested_url', 'https://example.org/other'), ('sha256', '0' * 64), ('bytes', len(raw) + 1), ('attempts', {})]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'does not identify'):
                self.invoke('source', *self.source_args(raw, dict(audit, **{field: changed})))

    def test_source_and_audit_size_limits_reject_before_read_or_persist(self):
        args = self.source_args()
        with open(args[1], 'wb') as file:
            file.truncate(10_000_001)
        with self.assertRaisesRegex(ValueError, 'Source exceeds'):
            self.invoke('source', *args)
        args = self.source_args(audit={})
        with open(args[-1], 'wb') as file:
            file.truncate(1_000_001)
        with self.assertRaisesRegex(ValueError, 'Retrieval record exceeds'):
            self.invoke('source', *args)
        self.artifact.assert_not_called()

    def test_local_campaign_executes_frozen_input_and_failed_runs_remain_saved(self):
        raw, protocol, campaign, args = self.campaign_args()
        value, output = self.invoke('run', *args)
        self.assertEqual(value['run']['status'], 'complete')
        self.assertEqual(value['run']['input_sha256'], workflow.digest(raw))
        self.assertFalse(json.loads(output)['persisted'])
        self.append_bundle.assert_not_called()
        Path(args[1]).write_bytes(raw + b' ')
        output_file = self.output()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.invoke('run', *args, output=output_file)
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(json.loads(Path(output_file).read_bytes())['run']['status'], 'failed')

    def test_persisted_campaign_retains_original_and_parsed_artifacts_with_measurements(self):
        raw, protocol, campaign, args = self.campaign_args()
        self.rows.side_effect = [[], [campaign], [protocol]]
        value, _ = self.invoke('run', *args, persist=True)
        self.assertEqual(self.artifact.call_count, 2)
        self.assertEqual(self.artifact.call_args_list[0].args[2], raw)
        entries = self.append_bundle.call_args.args[2]
        self.assertEqual([entry['kind'] for entry in entries[:3]], ['artifacts', 'artifacts', 'runs'])
        self.assertEqual(next(entry['record'] for entry in entries if entry['kind'] == 'measurements')['value'], 0)
        self.assertEqual(value['run']['status'], 'complete')

    def test_recorded_run_is_returned_without_reexecution_or_new_artifacts(self):
        raw, _, campaign, args = self.campaign_args()
        prior = dict(id=self.request, campaign_id=campaign['id'], input_sha256=workflow.digest(raw), status='complete')
        self.rows.return_value = [prior]
        with patch.object(cli.runner, 'execute') as execute:
            value, output = self.invoke('run', *args, persist=True)
            self.assertTrue(value['replayed'])
            self.assertTrue(json.loads(output)['replayed'])
            execute.assert_not_called()
        self.artifact.assert_not_called()
        for field, change in [('input_sha256', '0' * 64), ('campaign_id', str(uuid.uuid4()))]:
            self.rows.return_value = [dict(prior, **{field: change})]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'different inputs'):
                self.invoke('run', *args, persist=True)

    def test_campaign_requires_the_stored_frozen_protocol_and_configuration(self):
        _, protocol, campaign, args = self.campaign_args()
        for saved_campaign, saved_protocol in [([], [protocol]), ([dict(campaign, planned_units=2)], [protocol]),
                ([campaign], []), ([campaign], [dict(protocol, document={})]), ([campaign], [dict(protocol, sha256='0' * 64)])]:
            self.rows.side_effect = [[], saved_campaign, saved_protocol]
            with self.subTest(campaign=saved_campaign, protocol=saved_protocol), self.assertRaisesRegex(ValueError, 'stored frozen configuration'):
                self.invoke('run', *args, persist=True)
        self.append_bundle.assert_not_called()

    def test_campaign_rejects_nonowner_and_oversized_input_before_execution(self):
        _, _, _, args = self.campaign_args()
        self.owns.return_value = False
        with self.assertRaisesRegex(ValueError, 'ownership mismatch'):
            self.invoke('run', *args, persist=True)
        with open(args[1], 'wb') as file:
            file.truncate(10_000_001)
        with self.assertRaisesRegex(ValueError, 'hard operator limit'):
            self.invoke('run', *args)

    def test_preparation_preview_uses_registered_and_uploaded_original_plan_bytes(self):
        plan = small_plan()
        args = ['--paper-url', 'https://pure.mpg.de/pubman/item/item_3670144_1', '--request-id', self.request]
        with patch.object(cli.preparation, 'registered', return_value=(plan, workflow.canonical(plan))):
            value, output = self.invoke('prepare-plan', *args)
            self.assertEqual(json.loads(output)['jobs'], 1)
            self.assertEqual(value['manifest_sha256'], workflow.digest(workflow.canonical(plan)))
        value, _ = self.invoke('prepare-plan', *args, '--plan', self.file('plan.json', plan))
        self.assertEqual(len(value['jobs']), 1)
        self.rpc.assert_not_called()

    def test_persisted_preparation_exports_retained_timestamps_and_enqueues_only_unqueued_jobs(self):
        plan = small_plan(); raw = workflow.canonical(plan)
        paper_url = 'https://pure.mpg.de/pubman/item/item_3670144_1'
        study = dict(id=self.study, paper_url=paper_url)
        compiled = cli.preparation.compile_plan(plan, raw, study)
        retained = copy.deepcopy(compiled)
        retained['retained_marker'] = 'original freeze timestamp fixture'
        metadata = {'id': compiled['id']}
        self.rows.side_effect = [[study], [metadata]]
        jobs = [{'id': 'new-job', 'status': 'not-queued'}, {'id': 'existing-job', 'status': 'queued'}]
        with patch.object(cli.preparation, 'prepare', return_value={'prepared': {'jobs': jobs}}) as prepare, \
                patch.object(cli.preparation, 'compiled', return_value=retained), \
                patch.object(cli.preparation, 'enqueue') as enqueue, \
                patch.object(cli.preparation, 'status', return_value={'prepared': [{'id': 'other', 'queued_jobs': 0}, {'id': compiled['id'], 'queued_jobs': 2}]}):
            value, output = self.invoke('prepare-plan', '--paper-url', paper_url, '--plan', self.file('plan.json', plan),
                '--request-id', self.request, '--enqueue', persist=True)
            self.assertEqual(value, retained)
            self.assertEqual(json.loads(output)['queued_jobs'], 2)
            self.assertEqual(prepare.call_args.args[2]['plan_input'], raw.decode())
            enqueue.assert_called_once_with(study, self.actor, {'action': 'enqueue', 'prepared_id': compiled['id'], 'job_id': 'new-job'})

    def test_preparation_errors_do_not_overwrite_or_queue_unconfirmed_work(self):
        paper_url = 'https://pure.mpg.de/pubman/item/item_3670144_1'
        args = ['--paper-url', paper_url, '--request-id', self.request]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.invoke('prepare-plan', *args, '--enqueue')
        self.assertEqual(error.exception.code, 2)
        self.owns.return_value = False
        with self.assertRaisesRegex(ValueError, 'ownership mismatch'):
            self.invoke('prepare-plan', *args, persist=True)
        self.owns.return_value = True
        for studies in ([], [{'paper_url': 'https://example.org/other'}]):
            self.rows.return_value = studies
            with self.subTest(studies=studies), self.assertRaisesRegex(ValueError, 'different paper'):
                self.invoke('prepare-plan', *args, persist=True)
        with patch.object(cli.preparation, 'registered', return_value=(None, None)), self.assertRaisesRegex(ValueError, 'No exact registered'):
            self.invoke('prepare-plan', *args)
        plan_path = self.file('plan.json', {})
        with open(plan_path, 'wb') as file:
            file.truncate(cli.preparation.MAX_UPLOADED_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '250 KB limit'):
            self.invoke('prepare-plan', *args, '--plan', plan_path)
        plan = small_plan()
        self.rows.side_effect = [[dict(id=self.study, paper_url=paper_url)], []]
        with patch.object(cli.preparation, 'prepare', return_value={'prepared': {'jobs': []}}), self.assertRaisesRegex(cli.store.StoreError, 'was not confirmed'):
            self.invoke('prepare-plan', *args, '--plan', self.file('plan.json', plan), persist=True)

    def test_persisted_registered_preparation_without_enqueue_keeps_the_retained_plan(self):
        plan = small_plan(); raw = workflow.canonical(plan)
        study = dict(id=self.study, paper_url='https://pure.mpg.de/pubman/item/item_3670144_1')
        value = cli.preparation.compile_plan(plan, raw, study)
        self.rows.side_effect = [[study], [{'id': value['id']}]]
        with patch.object(cli.preparation, 'registered', return_value=(plan, raw)), \
                patch.object(cli.preparation, 'prepare', return_value={'prepared': {'jobs': []}}) as prepare, \
                patch.object(cli.preparation, 'compiled', return_value=value), patch.object(cli.preparation, 'enqueue') as enqueue:
            output, _ = self.invoke('prepare-plan', '--paper-url', study['paper_url'], '--request-id', self.request, persist=True)
            self.assertEqual(output, value)
            self.assertNotIn('plan_input', prepare.call_args.args[2])
            enqueue.assert_not_called()

    def test_cli_entrypoint_help_does_not_access_external_services(self):
        with patch.object(sys, 'argv', ['cli.py', '--help']), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            runpy.run_path(cli.__file__, run_name='__main__')
        self.assertEqual(error.exception.code, 0)
        self.transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
