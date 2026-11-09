"""Frozen project validation and disposable-host launch, with no host execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generated_package as package


def files():
    return {'experiment.py': b'print("synthetic control")\n', 'requirements.lock': b'',
        'system-packages.lock.json': b'[]', 'build-evidence.json': b'{"commands":[],"checks":[],"limitations":["Synthetic control"]}'}


def archive(entries):
    output = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as result:
        warnings.simplefilter('ignore', UserWarning)
        for name, raw in entries: result.writestr(name, raw)
    return output.getvalue()


class FrozenPackageBoundaries(unittest.TestCase):
    def test_archive_rejects_invalid_paths_links_duplicates_binary_and_excessive_expansion(self):
        link = zipfile.ZipInfo('link'); link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [([('experiment.py', b'pass'), ('experiment.py', b'pass')], 'duplicate'),
            ([('../outside', b'x')], 'relative'), ([('/absolute', b'x')], 'relative'),
            ([('a\\b', b'x')], 'relative'), ([('a//b', b'x')], 'relative'),
            ([('directory/', b'')], 'relative'), ([(link, b'outside')], 'relative'),
            ([('binary', b'\xff')], 'UTF-8'), ([], 'needs'),
            ([(str(i), b'x') for i in range(65)], 'bound'),
            ([('large', b'x' * 256001)], 'bound'),
            ([(str(i), b'x' * 250000) for i in range(5)], 'expanded')]
        for entries, reason in cases:
            with self.subTest(reason=reason, names=[str(name) for name, _ in entries][:3]), self.assertRaisesRegex(ValueError, reason):
                package.members(archive(entries))
        with self.assertRaisesRegex(ValueError, '256 KB'): package.members(b'x' * 256001)

    def test_dependency_locks_require_exact_versions_hashes_and_structured_evidence(self):
        original = files()
        for name, raw, reason in [('requirements.lock', b'package>=1', 'exact version'),
            ('system-packages.lock.json', b'{}', 'apt'),
            ('system-packages.lock.json', b'["curl"]', 'apt'),
            ('system-packages.lock.json', json.dumps(['curl=1'] * 33).encode(), 'apt'),
            ('build-evidence.json', b'[]', 'Build evidence'),
            ('build-evidence.json', b'{"commands":[],"checks":[],"limitations":true}', 'Build evidence')]:
            with self.subTest(name=name, raw=raw), self.assertRaisesRegex(ValueError, reason):
                package.members(archive(dict(original, **{name: raw}).items()))
        original['requirements.lock'] = b'\n# Original lock comment\npackage[extra]==1.2 --hash=sha256:' + b'a' * 64 + b'\n'
        original['system-packages.lock.json'] = b'["curl=1:2.3-1"]'
        self.assertEqual(package.members(archive(original.items())), original)

    def test_freeze_is_deterministic_and_refuses_dependency_directories_symlinks_and_size_overruns(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'sub').mkdir()
            for name, raw in files().items(): (root / name).write_bytes(raw)
            first = package.freeze(root); self.assertEqual(package.freeze(root), first)
            with zipfile.ZipFile(io.BytesIO(first)) as result:
                self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in result.infolist()))
            link = root / 'link'; link.symlink_to('experiment.py')
            with self.assertRaisesRegex(ValueError, 'symlinks'): package.freeze(root)
            link.unlink(); (root / 'node_modules').mkdir(); installed = root / 'node_modules/package.js'; installed.write_text('pass')
            with self.assertRaisesRegex(ValueError, 'installations'): package.freeze(root)
            installed.unlink(); large = root / 'large'; large.write_bytes(b'x' * 256001)
            with self.assertRaisesRegex(ValueError, 'file exceeds'): package.freeze(root)
            large.unlink()
            with patch.object(package, 'MAX_ARCHIVE', 1), self.assertRaisesRegex(ValueError, 'transport'): package.freeze(root)

    def test_disposable_host_wrapper_uses_hash_locked_installs_and_execs_only_the_frozen_entrypoint(self):
        for dependencies in [False, True]:
            with self.subTest(dependencies=dependencies), tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
                root = Path(folder).resolve(); inputs = root / 'inputs'; inputs.mkdir()
                source = files()
                source['sub/data.txt'] = b'Original retained UTF-8 input'
                if dependencies:
                    source['requirements.lock'] = b'package==1.2 --hash=sha256:' + b'a' * 64
                    source['system-packages.lock.json'] = b'["curl=1.2"]'
                (inputs / 'project.zip').write_bytes(archive(source.items()))
                stack.enter_context(patch.object(package.Path, 'cwd', return_value=root))
                stack.enter_context(patch.dict(os.environ, JUMPSERVE_INPUTS=str(inputs)))
                stack.enter_context(patch.object(sys, 'argv', ['package.py', '--spec', 'spec.json']))
                run = stack.enter_context(patch.object(package.subprocess, 'run'))
                change = stack.enter_context(patch.object(package.os, 'chdir'))
                execute = stack.enter_context(patch.object(package.os, 'execve'))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                package.run()
                built = root / 'built-project'
                self.assertEqual((built / 'sub/data.txt').read_bytes(), source['sub/data.txt'])
                self.assertEqual(json.loads(output.getvalue())['python_dependencies'], 'hash-locked' if dependencies else 'stdlib-only')
                change.assert_called_once_with(built)
                python = str(built / '.venv/bin/python') if dependencies else sys.executable
                self.assertEqual(execute.call_args.args[:2], (python, [python, str(built / 'experiment.py'), '--spec', 'spec.json']))
                self.assertEqual(execute.call_args.args[2]['JUMPSERVE_INPUTS'], str(inputs))
                if dependencies:
                    self.assertEqual(run.call_count, 5)
                    self.assertIn('--require-hashes', run.call_args_list[3].args[0])
                    self.assertEqual(run.call_args_list[1].args[0], ['apt-get', 'install', '-y', '--no-install-recommends', 'curl=1.2'])
                    self.assertTrue(all(call.kwargs['check'] for call in run.call_args_list))
                else: run.assert_not_called()


if __name__ == '__main__': unittest.main()
