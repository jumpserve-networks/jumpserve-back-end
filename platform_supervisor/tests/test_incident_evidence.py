"""Original passive evidence, complete pairs, bounded expansion and redaction."""
import gzip
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import uuid
import zipfile
from platform_supervisor import incident_evidence as evidence,repair
from platform_supervisor.common import canonical,digest,Hold
from platform_supervisor.tests.test_repair_handoff import Store


def archive(files):
    sink=io.BytesIO()
    with zipfile.ZipFile(sink,'w',zipfile.ZIP_DEFLATED) as z:
        for name,raw in files.items():z.writestr(name,raw)
    return sink.getvalue()


def history_files(history):
    raw=canonical(history);compressed=gzip.compress(raw,mtime=0)
    index=dict(version='builder-history-gzip-v1',path='native-tool-history.json.gz',sha256=digest(raw),bytes=len(raw),records=len(history),compressed_sha256=digest(compressed))
    return {'native-tool-history.json':canonical(index),'native-tool-history.json.gz':compressed}


class IncidentEvidence(unittest.TestCase):
    def test_delivered_helper_metadata_and_recent_pairs_survive_compressed_history(self):
        history=[]
        for i in range(20):history.extend([dict(type='function_call',call_id='call-'+str(i),name='exec',arguments=json.dumps(dict(command='synthetic-'+str(i)))),dict(type='function_call_output',call_id='call-'+str(i),output=str(i))])
        files={'finish-repair.py':'raise RuntimeError("never execute a diagnostic")','work-packet.json':'{"ready":false,"count":0}',
               'repair-test-plan.json':'{"incident":"retained"}','project/experiment.py':'def original(): return 0',**history_files(history)}
        value=evidence.checkpoint_view(archive(files));view=value['native_history']
        self.assertIn('finish-repair.py',value['delivered_files']);self.assertEqual(value['delivered_files']['work-packet.json']['value'],dict(ready=False,count=0))
        self.assertEqual(view['records'],40);self.assertEqual(view['shown_pairs'],8);self.assertFalse(view['complete'])
        self.assertEqual(view['pairs'][-1]['request']['value'],history[-2]);self.assertEqual(view['pairs'][-1]['output']['value'],history[-1])
        self.assertEqual(view['original_sha256'],digest(canonical(history)));self.assertEqual(view['pairs'][-1]['output']['ordinal'],39)

    def test_missing_output_remains_missing_and_large_pair_content_has_original_hash(self):
        history=[dict(type='function_call',call_id='last',name='read',arguments='x'*15000)]
        pair=evidence.history_view(canonical(history))['pairs'][0]
        self.assertIsNone(pair['output']);self.assertFalse(pair['output_recorded']);self.assertFalse(pair['request']['complete'])
        self.assertEqual(pair['request']['original_sha256'],digest(canonical(history[0])))
        orphan=evidence.history_view(canonical([dict(type='function_call_output',call_id='absent',output='retained')]))
        self.assertEqual(orphan['orphan_outputs'],1);self.assertFalse(orphan['complete'])

    def test_corrupt_or_expanding_history_is_fenced_before_model_delivery(self):
        history=[dict(type='function_call',call_id='one',name='read',arguments='{}')]
        for defect in ('compressed-hash','expanded-hash','record-count','missing-member','over-bound'):
            with self.subTest(defect=defect):
                files=history_files(history);index=json.loads(files['native-tool-history.json'])
                if defect=='compressed-hash':index['compressed_sha256']='0'*64
                elif defect=='expanded-hash':index['sha256']='0'*64
                elif defect=='record-count':index['records']+=1
                elif defect=='missing-member':files.pop('native-tool-history.json.gz')
                else:
                    expanded=b' '*(evidence.HISTORY_BYTES+1);files['native-tool-history.json.gz']=gzip.compress(expanded,mtime=0)
                    index.update(bytes=evidence.HISTORY_BYTES,compressed_sha256=digest(files['native-tool-history.json.gz']))
                files['native-tool-history.json']=canonical(index)
                with self.assertRaises(Hold):evidence.checkpoint_view(archive(files))

    def test_ambiguous_history_identity_cannot_be_silently_paired(self):
        record=dict(type='function_call',call_id='same',name='exec',arguments='{}')
        with self.assertRaisesRegex(Hold,'duplicate'):evidence.history_view(canonical([record,record]))

    def test_unsafe_zip_paths_duplicates_and_links_remain_fenced(self):
        for name in ('../finish-repair.py','/finish-repair.py','project\\escape.py'):
            with self.subTest(name=name),self.assertRaises(Hold):evidence.checkpoint_view(archive({name:'untrusted'}))
        sink=io.BytesIO()
        with zipfile.ZipFile(sink,'w') as z:
            link=zipfile.ZipInfo('finish-repair.py');link.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(link,'/sensitive')
        with self.assertRaises(Hold):evidence.checkpoint_view(sink.getvalue())

    def test_real_delivery_redacts_secrets_in_helpers_and_paired_outputs_without_executing(self):
        history=[dict(type='function_call',call_id='call-1',name='exec',arguments='{}'),dict(type='function_call_output',call_id='call-1',output='email=person@example.invalid Authorization: Bearer synthetic-secret-token')]
        checkpoint=archive({'finish-repair.py':'raise Exception("diagnostic only")',**history_files(history)})
        bootstrap=b'Trusted instructions and email=person@example.invalid'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root);store.research={}
            for name,raw in [('checkpoint.zip',checkpoint),('task.txt',bootstrap),('context-delivery.json',b'{"version":"historical"}')]:
                h=digest(raw);path=store.packet['assessment_id']+'/'+h
                store.packet['artifacts'].append(dict(id=str(uuid.uuid4()),sha256=h,byte_count=len(raw),storage_path=path,provenance=dict(filename=name)))
                store.research['/storage/v1/object/research-workflow-raw/'+path]=raw
            repair.deliver(store,root,{})
            contents=b'\n'.join(p.read_bytes() for p in (root/'references/assessment-repair/artifacts').glob('*.json'))
            self.assertIn(b'finish-repair.py',contents);self.assertIn(b'output_recorded',contents)
            self.assertNotIn(b'person@example.invalid',contents);self.assertNotIn(b'synthetic-secret-token',contents)
            self.assertFalse((root/'finish-repair.py').exists())
            manifest=json.loads((root/'references/assessment-repair/artifacts'/(digest(b'{"version":"historical"}')+'.json')).read_bytes())
            self.assertNotIn('runtime_sources',manifest['view']['value'])


if __name__=='__main__':unittest.main()
