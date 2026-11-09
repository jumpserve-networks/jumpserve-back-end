"""The selected tracer measures both outcomes without hiding untested guards."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest
import coverage

ROOT=Path(__file__).resolve().parents[1]
SOURCE='from io import StringIO\ndef check(value):\n    with StringIO() as stream:\n        if value: raise ValueError("expected")\n    return 1\n'


class CoverageTracerContract(unittest.TestCase):
    def test_conditional_exception_under_with_retains_each_real_untested_outcome(self):
        configured=coverage.Coverage(config_file=str(ROOT/'.coveragerc'))
        self.assertEqual(configured.get_option('run:core'),'pytrace')
        for values,expected in [([False],[[4,-2]]),([True],[[4,5]]),([False,True],[])]:
            with self.subTest(values=values),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'guard.py';path.write_text(SOURCE)
                measured=coverage.Coverage(config_file=False,data_file=None,branch=True,source=[directory]);measured.set_option('run:core',configured.get_option('run:core'))
                measured.start()
                try:
                    check=runpy.run_path(str(path))['check']
                    for value in values:
                        try:check(value)
                        except ValueError:pass
                finally:measured.stop()
                output=Path(directory)/'report.json';measured.json_report(outfile=str(output));files=json.loads(output.read_text())['files'];self.assertEqual(len(files),1);record=next(iter(files.values()))
                self.assertEqual(record['missing_branches'],expected);self.assertEqual(record['summary']['num_branches'],2)


if __name__=='__main__':unittest.main()
