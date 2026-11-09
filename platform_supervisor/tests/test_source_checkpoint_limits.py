"""Source/checkpoint size limits are enforced on original archive metadata."""
import io
import base64
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock
import zipfile
from platform_supervisor import source,worker
from platform_supervisor.common import canonical,Hold,POLICY
from platform_supervisor.tests.test_worker_boundaries import FixtureStore,REPO


class SourceCheckpointLimits(unittest.TestCase):
    def test_snapshots_refuse_linked_roots_directory_links_and_oversized_original_files(self):
        for defect in ['root','directory','bytes']:
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);directory=root/'source';directory.mkdir()
                if defect=='root':
                    link=root/'linked-source';link.symlink_to(directory,target_is_directory=True);directory=link
                elif defect=='directory':(directory/'linked-child').symlink_to(root,target_is_directory=True)
                else:(directory/'large.txt').write_bytes(b'x'*20000001)
                with self.assertRaises(Hold):source.snapshot(directory)

    def test_patch_file_and_compressed_checkpoint_byte_bounds_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for index in range(POLICY['maximum_changed_files']+1):(root/f'app-{index}.ts').write_text('original = 1\n')
            with self.assertRaisesRegex(Hold,'patch limits'):source.freeze({},root)
        with self.assertRaisesRegex(Hold,'private artifact limit'):source.checkpoint({},dict(original=base64.b64encode(os.urandom(5200000)).decode()))

    def test_source_deletion_uses_only_the_declared_original_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original=root/'app.ts';original.write_bytes(b'Original retained code');other=root/'other.ts';other.write_bytes(b'Other unchanged code')
            source.apply_patch(dict(files=[dict(path='app.ts',content=None,mode='100644')]),root)
            self.assertFalse(original.exists());self.assertEqual(other.read_bytes(),b'Other unchanged code')

    def test_working_checkpoint_guards_keep_oversized_or_linked_workspaces_local(self):
        for defect in ['root-link','packet-link','source-size','source-count','metadata-link','metadata-size']:
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);workspace=root/'workspace';workspace.mkdir();store=FixtureStore(root);controller=worker.Controller(store,Mock(),'fixed-image');controller.state={'sources':{REPO:{'snapshot':{}}}};repo=workspace/POLICY['repositories'][REPO]['directory'];repo.mkdir()
                if defect=='root-link':(workspace/'linked').symlink_to(root,target_is_directory=True)
                elif defect=='packet-link':
                    packet=workspace/'references';packet.mkdir();(packet/'linked').symlink_to(root)
                elif defect=='source-size':(repo/'app.ts').write_bytes(b'x'*3000001)
                elif defect=='source-count':
                    for index in range(151):(repo/f'app-{index}.ts').write_bytes(b'Original code')
                elif defect=='metadata-link':(workspace/'progress.json').symlink_to(root/'original-private')
                else:(workspace/'progress.json').write_bytes(b'x'*3000001)
                with self.assertRaises(Hold):
                    if defect in ['root-link','packet-link']:controller.validate_workspace(workspace)
                    else:controller.persist_work(workspace)
                self.assertNotIn('working_artifact',controller.state);self.assertEqual(store.artifacts,{})

    def test_restore_requires_the_original_checkpoint_hash_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);workspace=root/'workspace';workspace.mkdir();store=FixtureStore(root);controller=worker.Controller(store,Mock(),'fixed-image');artifact=store.artifact('original-checkpoint.zip',b'Original checkpoint')
            controller.state=dict(working_artifact=dict(path=artifact['storage_path'],sha256='a'*64))
            with self.assertRaisesRegex(Hold,'hash differs'):controller.restore_work(workspace)
            self.assertEqual(list(workspace.iterdir()),[])

    def test_archive_aggregate_limit_stops_before_writing_an_eleventh_large_source(self):
        output=io.BytesIO();payload=b'x'*20000000
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            root=tarfile.TarInfo('root');root.type=tarfile.DIRTYPE;archive.addfile(root)
            for index in range(11):
                member=tarfile.TarInfo('root/file-'+str(index)+'.txt');member.size=len(payload);archive.addfile(member,io.BytesIO(payload))
        with tempfile.TemporaryDirectory() as temporary,self.assertRaisesRegex(Hold,'installed source limit'):
            source.extract(output.getvalue(),temporary)

    def test_restore_rejects_excessive_members_and_excluded_source_paths_before_writing(self):
        for defect in ['member-count','excluded-path']:
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);store=FixtureStore(root);controller=worker.Controller(store,Mock(),'fixed-image');workspace=root/'workspace';workspace.mkdir()
                output=io.BytesIO()
                with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                    patch={'files':[{'path':'.env.private','content':'eA==','mode':'100644'}]} if defect=='excluded-path' else {'files':[]}
                    archive.writestr('jumpserve-front-end.json',canonical(patch));archive.writestr('work-packet.json','{}')
                    if defect=='member-count':
                        for index in range(3):archive.writestr('extra-'+str(index),b'')
                artifact=store.artifact('working-checkpoint.zip',output.getvalue());controller.state={'sources':{REPO:{}},'working_artifact':{'path':artifact['storage_path'],'sha256':artifact['sha256']}}
                with self.assertRaisesRegex(Hold,'expansion exceeds|unsafe path'):controller.restore_work(workspace)
                self.assertEqual(list(workspace.iterdir()),[])


if __name__=='__main__':unittest.main()
