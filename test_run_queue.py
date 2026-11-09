"""Queue configuration, bounded execution and local CLI behavior; no real jobs run."""
import argparse
import contextlib
import io
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_queue as q
import yaml


class QueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='jumpserve-queue-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.queue = self.root / 'queues'
        self.queue.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(q, 'PROJECT_DIR', self.root).start()
        patch.object(q, 'QUEUE_DIR', self.queue).start()
        self.output = io.StringIO()
        context = contextlib.redirect_stdout(self.output)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def config(self, value, name='scenario.yaml', directory=None):
        path = (directory or self.queue) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding='utf8')
        return path

    def job(self):
        return q.Job(**dict(name='synthetic', run='never-execute', cwd=self.root, env={}, retries=0,
                           continue_on_error=False, timeout_seconds=None))

    def test_defaults_and_job_overrides_preserve_working_directory_environment_and_limits(self):
        config = self.config({'defaults': {'cwd': 'work', 'env': {'SHARED': 1, 'REPLACED': 'old'},
                                          'retries': 2, 'continue_on_error': True, 'timeout_seconds': 10},
                              'jobs': [{'name': ' first ', 'run': ' echo synthetic ', 'env': {'REPLACED': 'new'},
                                        'cwd': 'child', 'retries': 1, 'continue_on_error': False, 'timeout_seconds': 0.5}]})
        [job] = q.load_jobs(config)
        self.assertEqual((job.name, job.run, job.cwd), ('first', 'echo synthetic', self.root / 'work/child'))
        self.assertEqual(job.env, {'SHARED': '1', 'REPLACED': 'new'})
        self.assertEqual((job.retries, job.continue_on_error, job.timeout_seconds), (1, False, 0.5))

    def test_parameter_commands_quote_scalar_and_list_values_without_running_shell_fragments(self):
        path = self.config({'defaults': None, 'jobs': [{'params': {'num_clients': 2, '--name': "an author's $(example)",
                      '-flag': True, 'off': False, 'missing': None, 'values': [1, 2.5, True, 'x']},
                      'python': 'python executable', 'script': 'runner name.py', 'sudo': True}]})
        [job] = q.load_jobs(path)
        self.assertEqual(shlex.split(job.run), ['sudo', 'python executable', 'runner name.py', '--num-clients', '2',
                          '--name', "an author's $(example)", '--flag', '--values', '1,2.5,True,x'])
        self.assertEqual(job.name, 'job-1')
        self.assertEqual(job.cwd, self.root)

    def test_parameter_defaults_and_absolute_external_config_directory(self):
        external = self.root / 'external'
        path = self.config({'jobs': [{'params': {'empty': ''}, 'script': 'explicit-runner.py', 'cwd': str(self.root)}]}, directory=external)
        [job] = q.load_jobs(path)
        self.assertEqual(shlex.split(job.run), ['python3', 'explicit-runner.py', '--empty'])
        self.assertEqual(job.cwd, self.root)
        self.assertEqual(q._default_base_dir(path), external)
        self.assertEqual(q._as_str_dict(None, 'env'), {})

    def test_invalid_configuration_is_rejected_before_execution(self):
        invalid = [None, [], {'defaults': []}, {'jobs': []}, {'jobs': 'invalid'}, {'jobs': [None]},
                   {'jobs': [{}]}, {'jobs': [{'run': 'x', 'params': {}}]}, {'jobs': [{'run': ' '}]},
                   {'jobs': [{'run': 1}]}, {'jobs': [{'run': 'x', 'name': ''}]}, {'jobs': [{'run': 'x', 'name': 1}]}]
        for field, values in {
            'env': [[], {1: 'invalid'}, {'x': {}}], 'retries': [True, 0.5, -1],
            'continue_on_error': ['true'], 'timeout_seconds': [True, 0, -1, '10'], 'cwd': ['', 1],
        }.items():
            invalid.extend({'jobs': [{'run': 'x', field: value}]} for value in values)
        for params in [[], {1: 2}, {'--': 1}, {'x': []}, {'x': [{}]}, {'x': {}}]:
            invalid.append({'jobs': [{'params': params, 'script': 'explicit-runner.py'}]})
        for field, value in [('python', ''), ('script', 1), ('sudo', 'true')]:
            invalid.append({'jobs': [{'params': {}, 'script': 'explicit-runner.py', field: value}]})
        invalid.append({'jobs': [{'params': {}}]})
        with patch.object(q.subprocess, 'run') as execute:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    q.load_jobs(self.config(value))
            execute.assert_not_called()

    def test_scenario_resolution_handles_shorthand_suffixes_nested_paths_and_missing_files(self):
        path = self.config({'jobs': [{'run': 'x'}]}, 'sample.yml')
        with contextlib.chdir(self.root):
            resolved, candidates = q.resolve_config_path('sample')
            self.assertEqual(resolved, path)
            self.assertIn(self.queue / 'sample.yml', candidates)
            self.assertEqual(q.resolve_config_path(str(path))[0], path)
            self.assertEqual(q.resolve_config_path('sample.yml')[0], path)
            missing, attempts = q.resolve_config_path('missing')
            self.assertEqual(missing, self.root / 'missing')
            self.assertGreater(len(attempts), 1)
        with contextlib.chdir(self.queue):
            candidates = q._config_candidates('sample')
            self.assertEqual(len({p.resolve() for p in candidates}), len(candidates))
        self.config({}, 'nested/other.yaml')
        (self.queue / 'ignore.txt').write_text('fixture')
        self.assertEqual(len(q.list_queue_scenarios()), 2)
        for prefix in ['sample', 'queues/sample', 'sample.yml', 'queues\\sample']:
            self.assertEqual(q.matching_queue_scenarios(prefix), [path])
        self.assertEqual(len(q.matching_queue_scenarios(None)), 2)
        with patch.object(q, 'QUEUE_DIR', self.root / 'absent'):
            self.assertEqual(q.list_queue_scenarios(), [])

    def test_dry_run_never_invokes_shell(self):
        with patch.object(q.subprocess, 'run') as execute:
            self.assertEqual(q.run_jobs([self.job()], dry_run=True), 0)
            execute.assert_not_called()

    def test_retry_uses_configured_timeout_directory_and_merged_environment(self):
        job = self.job()
        job.retries, job.timeout_seconds, job.env = 1, 3, {'JUMPSERVE_UNIT_VALUE': 'fixture'}
        with patch.object(q.subprocess, 'run', side_effect=[subprocess.CompletedProcess('x', 2), subprocess.CompletedProcess('x', 0)]) as execute:
            self.assertEqual(q.run_jobs([job], dry_run=False), 0)
            self.assertEqual(execute.call_count, 2)
            self.assertEqual(execute.call_args.kwargs['timeout'], 3)
            self.assertEqual(execute.call_args.kwargs['cwd'], self.root)
            self.assertEqual(execute.call_args.kwargs['env']['JUMPSERVE_UNIT_VALUE'], 'fixture')
            self.assertEqual(execute.call_args.kwargs['executable'], '/bin/bash')

    def test_timeout_stops_queue_and_continuation_reports_failure(self):
        with patch.object(q.subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic', 1)) as execute:
            self.assertEqual(q.run_jobs([self.job(), self.job()], False), 124)
            execute.assert_called_once()
        job = self.job(); job.continue_on_error = True
        with patch.object(q.subprocess, 'run', side_effect=[subprocess.CompletedProcess('x', 7), subprocess.CompletedProcess('x', 0)]) as execute:
            self.assertEqual(q.run_jobs([job, self.job()], False), 1)
            self.assertEqual(execute.call_count, 2)

    def test_scenarios_stop_on_invalid_config_or_failed_job_and_print_relative_and_external_paths(self):
        first = self.config({'jobs': [{'run': 'x'}]}, 'first.yaml')
        second = self.config({'jobs': [{'run': 'y'}]}, 'second.yaml')
        with patch.object(q, 'run_jobs', return_value=0) as run:
            self.assertEqual(q.run_scenarios([first, second], True), 0)
            self.assertEqual(run.call_count, 2)
        with patch.object(q, 'run_jobs', return_value=9) as run:
            self.assertEqual(q.run_scenarios([first, second], False), 9)
            run.assert_called_once()
        invalid = self.config(None, 'invalid.yaml')
        with contextlib.redirect_stderr(io.StringIO()), patch.object(q, 'run_jobs') as run:
            self.assertEqual(q.run_scenarios([invalid], False), 2)
            run.assert_not_called()
        self.assertEqual(q._display_config_path(first), 'queues/first.yaml')
        self.assertEqual(q._display_config_path(Path('/outside/scenario.yaml')), '/outside/scenario.yaml')

    def test_cli_argument_validation_and_main_modes(self):
        with patch.object(sys, 'argv', ['run_queue.py', '--dry-run']), contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit) as error:
                q.parse_args()
            self.assertEqual(error.exception.code, 2)
            self.assertIn('Choose an explicit scenario', errors.getvalue())
        with patch.object(sys, 'argv', ['run_queue.py', '--dry-run', '--prefix', 'sample']):
            self.assertTrue(q.parse_args().dry_run)
        with patch.object(sys, 'argv', ['run_queue.py', 'sample', '--prefix', 'sample']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                q.parse_args()
            self.assertEqual(error.exception.code, 2)
        scenario = self.config({'jobs': [{'run': 'x'}]}, 'sample.yaml')
        for list_mode, prefix in [(True, None), (True, 'sample'), (True, 'absent'), (False, 'absent')]:
            with self.subTest(list_mode=list_mode, prefix=prefix), patch.object(q, 'parse_args', return_value=argparse.Namespace(list=list_mode, prefix=prefix, config=None, dry_run=True)), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(q.main(), 2 if not list_mode else 0)
        with patch.object(q, 'parse_args', return_value=argparse.Namespace(list=True, prefix=None)), patch.object(q, 'matching_queue_scenarios', return_value=[]):
            self.assertEqual(q.main(), 0)
        for prefix, config in [('sample', None), (None, str(scenario))]:
            with patch.object(q, 'parse_args', return_value=argparse.Namespace(list=False, prefix=prefix, config=config, dry_run=True)), patch.object(q, 'run_scenarios', return_value=0) as run:
                self.assertEqual(q.main(), 0); run.assert_called_once_with([scenario], dry_run=True)
        with patch.object(q, 'parse_args', return_value=argparse.Namespace(list=False, prefix=None, config='absent', dry_run=True)), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(q.main(), 2)

    def test_actual_cli_entrypoint_lists_without_running_jobs(self):
        result = subprocess.run([sys.executable, str(Path(q.__file__)), '--list', '--prefix', '__synthetic_missing__'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No queue scenarios found', result.stdout)

    def test_archived_configuration_cannot_run_even_when_requested_explicitly(self):
        path=self.config({'jobs':[{'run':'never-execute'}]},'original.yaml',self.root/'archive/queues/retired')
        original=path.read_bytes()
        with patch.object(q.subprocess,'run') as execute,contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(q.run_scenarios([path],dry_run=False),2)
        self.assertIn('historical queue archive',errors.getvalue());execute.assert_not_called();self.assertEqual(path.read_bytes(),original)

    def test_retired_corpus_is_hash_preserved_and_every_listed_scenario_has_a_present_runner(self):
        root=Path(q.__file__).resolve().parent;manifest=json.loads((root/'archive/queues/retired/manifest.json').read_bytes())
        self.assertEqual(manifest['version'],'retired-queue-archive-v1');self.assertEqual(len(manifest['files']),29)
        for record in manifest['files']:
            raw=(root/record['archived_path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),record['sha256']);self.assertEqual(len(raw),record['bytes']);self.assertFalse((root/record['original_path']).exists())
        for scenario in (root/'queues').rglob('*.yaml'):
            data=yaml.safe_load(scenario.read_bytes())
            for job in data['jobs']:
                self.assertIn('script',job);self.assertTrue((root/job['script']).is_file(),str(scenario));self.assertNotEqual(job['script'],'netem_cubic_benchmark.py')

    def test_historical_programs_preserve_original_bytes_outside_supported_entrypoints(self):
        root=Path(q.__file__).resolve().parent
        manifest=json.loads((root/'archive/historical-tools/manifest.json').read_bytes())
        self.assertEqual(manifest['version'],'historical-tools-archive-v1')
        self.assertRegex(manifest['source_revision'],r'^[a-f0-9]{40}$')
        self.assertTrue(manifest['files']);originals=set()
        for row in manifest['files']:
            self.assertEqual(row['archived_path'],'archive/historical-tools/'+row['original_path'])
            self.assertNotIn(row['original_path'],originals);originals.add(row['original_path'])
            original=(root/row['original_path']).resolve();archived=(root/row['archived_path']).resolve()
            self.assertTrue(original.is_relative_to(root));self.assertTrue(archived.is_relative_to(root/'archive/historical-tools'))
            self.assertFalse(original.exists());self.assertFalse((root/row['archived_path']).is_symlink())
            raw=archived.read_bytes()
            self.assertEqual(len(raw),row['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),row['sha256'])
        tools=json.loads((root/'maintained-tools.json').read_bytes())
        self.assertEqual(tools['version'],'maintained-manual-tools-v1')
        for name in tools['manual_programs']:
            self.assertNotIn(name,originals);self.assertTrue((root/name).is_file(),name)
        for name in tools['protected_protocol_directories']:
            self.assertTrue((root/name).is_dir(),name)


if __name__ == '__main__':
    unittest.main()
