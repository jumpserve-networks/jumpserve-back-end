"""Run the real symlinked CLI regressions in the pinned native Node runtime."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE = ROOT / 'openclaw_worker/node_modules/node/bin/node'


class AuthEntrypoints(unittest.TestCase):
    def test_versioned_install_preserves_readiness_rotation_and_uncertainty(self):
        if not NODE.exists():
            self.skipTest('Pinned Node required; no real credentials are used.')
        result = subprocess.run(
            [str(NODE), '--test', str(Path(__file__).with_name('openclaw_auth_entrypoint_test.mjs'))],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('pass 4', result.stdout)


if __name__ == '__main__':
    unittest.main()
