"""Pinned passive source delivery rejects unsafe incomplete and excessive inputs."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from platform_supervisor import runtime_sources as sources,source,source_refresh
from platform_supervisor.common import Hold,digest
from platform_supervisor.tests import test_runtime_sources as fixtures


class RuntimeSourceBoundaries(unittest.TestCase):
    def fixture(self):
        case=fixtures.RuntimeSources();case.setUp();self.addCleanup(case.doCleanups);return case

    def test_source_paths_and_manifests_reject_unsafe_names_and_invalid_identity_before_delivery(self):
        case=self.fixture()
        for name in ['/absolute','../outside','a/../worker.py','worker\\file.py','./worker.py']:
            with self.subTest(name=name),self.assertRaises(Hold):sources.read_plain(case.installed,name)
        for change in [{'version':'foreign'},{'source_revision':'invalid'},{'files':{}},{'files':[]},{'bundle_files':[]},{'files':{'../worker.py':'a'*64}},{'files':{'worker.py':'a'*64}}]:
            case.store.request.return_value=dict(case.manifest,**change)
            with self.subTest(change=change),self.assertRaises(Hold):sources.installed(case.store,case.installed)
        case.controller.github.main.return_value='invalid'
        with self.assertRaisesRegex(Hold,'Exact infrastructure'):sources.infrastructure(case.controller)
        case.controller.github.archive.assert_not_called()

    def test_combined_installed_source_bound_is_checked_even_when_each_individual_module_is_valid(self):
        case=self.fixture();extra=b'Original passive module\n'*50000
        for name in ['first.py','second.py']:
            (case.installed/'platform_supervisor'/name).write_bytes(extra);case.manifest['files'][name]=digest(extra);case.manifest['bundle_files']['platform_supervisor/'+name]=digest(extra)
        with self.assertRaisesRegex(Hold,'delivery bound'):sources.installed(case.store,case.installed)

    def test_predecessor_archive_must_match_installed_modules_and_retain_required_nonlinked_tests(self):
        for mode in ['changed','missing','symlink','large']:
            with self.subTest(mode=mode):
                case=self.fixture();observation,_=sources.installed(case.store,case.installed);root=case.store.spool/case.store.task['id']/'runtime-source-cache'/('backend-'+observation['source_revision']);root.mkdir(parents=True)
                for name,raw in case.predecessor.items():path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                if mode=='changed':(root/'platform_supervisor/worker.py').write_bytes(b'Unverified predecessor')
                elif mode=='missing':(root/'platform_supervisor/tests/test_self_runtime.py').unlink()
                elif mode=='symlink':
                    tests=root/'platform_supervisor/tests';tests.rename(root/'original-tests');tests.symlink_to(root/'original-tests')
                else:
                    for name in ['test_self_runtime.py','test_supervisor.py']:(root/'platform_supervisor/tests'/name).write_bytes(b'x'*1000001)
                with self.assertRaises(Hold):sources.predecessor_tests(case.controller,observation)
                case.controller.github.archive.assert_not_called()

    def test_infrastructure_source_file_count_is_bounded_and_unrelated_private_files_are_omitted(self):
        case=self.fixture();root=case.store.spool/case.store.task['id']/'runtime-source-cache'/('b'*40);root.mkdir(parents=True)
        for name,raw in case.owning.items():path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        directory=root/'lib';directory.mkdir()
        for index in range(501):(directory/f'passive_{index}.ts').write_bytes(b'')
        with self.assertRaisesRegex(Hold,'delivery bound'):sources.infrastructure(case.controller)
        case.controller.github.archive.assert_not_called()

    def test_retained_destination_cannot_follow_a_link_even_when_its_contents_match(self):
        case=self.fixture();original=case.root/'original.py';original.write_bytes(b'Original passive source');destination=case.workspace/'linked.py';destination.symlink_to(original)
        with self.assertRaisesRegex(Hold,'destination contains a link'):sources.write_copy(case.workspace,'linked.py',original.read_bytes())
        self.assertEqual(original.read_bytes(),b'Original passive source')

    def test_changed_package_manifest_requires_its_original_validation_script_record(self):
        case=self.fixture();directory=case.workspace/'jumpserve-front-end';directory.mkdir();path=directory/'package.json';path.write_bytes(b'{"version":"1","scripts":{"test":"fixed-test"}}');baseline=source.snapshot(directory);path.write_bytes(b'{"version":"2","scripts":{"test":"fixed-test"}}')
        with self.assertRaisesRegex(Hold,'retained original validation scripts'):source.freeze(baseline,directory)

    def test_source_refresh_cannot_recover_when_both_original_directory_and_backup_are_missing(self):
        case=self.fixture()
        with self.assertRaisesRegex(Hold,'source and backup are unavailable'):source_refresh.replace(case.root/'missing',case.root/'staged',case.root/'backup',{}, {})


if __name__=='__main__':unittest.main()
