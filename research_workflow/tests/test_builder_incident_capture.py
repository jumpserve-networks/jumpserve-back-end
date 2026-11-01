"""Installed source fingerprints are retained before any provider call."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import experiment_builder as builder
from workflow import canonical,digest


class IncidentCapture(unittest.TestCase):
    def test_bootstrap_records_actual_installed_sources_and_preserves_full_original_context(self):
        context=dict(claim=dict(description='Synthetic source'),proposal_contract=dict(preserved=True))
        with tempfile.TemporaryDirectory() as tmp,patch.object(builder.runtime,'http_request') as provider:
            root=Path(tmp);text,manifest=builder.bootstrap(dict(system=[dict(text='Original trusted instruction')],builder=dict(context=context)),root,root,[])
            record=manifest['runtime_sources'];self.assertEqual(record['version'],'installed-builder-sources-v1')
            for item in record['files']:
                self.assertEqual(item['sha256'],digest(Path(builder.__file__).with_name(item['path']).read_bytes()))
            self.assertEqual(len(record['files']),5)
            self.assertEqual(manifest['bootstrap_sha256'],digest(text.encode()))
            self.assertEqual((root/'research-context.json').read_bytes(),canonical(context));provider.assert_not_called()


if __name__=='__main__':unittest.main()
