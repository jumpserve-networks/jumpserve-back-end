"""Local server and registered compiler entrypoints with network/writes intercepted."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import build_ipv6_plan,preparation,server,workflow


class LocalEntrypointBoundaries(unittest.TestCase):
    def test_local_server_honors_explicit_catalogue_origin_and_port_without_opening_a_socket(self):
        output=io.StringIO();listener=Mock()
        with patch.object(sys,'argv',['server.py','--catalogue-only','--origin','http://localhost:3000','--port','4102']),patch('http.server.ThreadingHTTPServer',return_value=listener) as create,contextlib.redirect_stdout(output):runpy.run_path(server.__file__,run_name='__main__')
        self.assertEqual(create.call_args.args[0],('127.0.0.1',4102));handler=create.call_args.args[1];self.assertTrue(handler.catalogue_only);self.assertEqual(handler.allowed_origin,'http://localhost:3000');listener.serve_forever.assert_called_once();self.assertEqual(json.loads(output.getvalue())['authentication'],'actual Supabase Google session; no test bypass')
        instance=object.__new__(server.Server);instance.path='/research/papers';instance.command='GET';instance.headers={'Content-Length':str(server.MAX_BODY+1)};instance.send_response=Mock();instance.end_headers=Mock()
        with patch.object(server,'handler') as dispatch:instance.handle_request()
        instance.send_response.assert_called_once_with(413);dispatch.assert_not_called()

    def test_registered_compiler_preserves_existing_versions_and_emits_identical_plan_bytes_when_destination_is_new(self):
        with patch.object(sys,'argv',['build_ipv6_plan.py']),self.assertRaisesRegex(SystemExit,'Preserve the registered plan'):runpy.run_path(build_ipv6_plan.__file__,run_name='__main__')
        original=gzip.decompress((Path(__file__).parent/'fixtures/ipv6-assessment-v2.json.gz').read_bytes());source=build_ipv6_plan.ROOT/'experiments/ipv6_dns/evidence/assessment-v2.json';destination=Path(build_ipv6_plan.__file__).parent/'plans/ipv6-archive-v1.json';read=Path.read_bytes;exists=Path.exists;captured={};output=io.StringIO()
        def write(path,raw):
            self.assertEqual(path,destination);captured[path]=raw;return len(raw)
        with patch.object(sys,'argv',['build_ipv6_plan.py']),patch.object(Path,'read_bytes',lambda path:original if path==source else read(path)),patch.object(Path,'exists',lambda path:False if path==destination else exists(path)),patch.object(Path,'write_bytes',write),contextlib.redirect_stdout(output):runpy.run_path(build_ipv6_plan.__file__,run_name='__main__')
        _,registered=preparation.registered({'paper_url':'https://pure.mpg.de/pubman/item/item_3670144_1'});self.assertEqual(captured[destination],registered);receipt=json.loads(output.getvalue());self.assertEqual(receipt['plan_sha256'],workflow.digest(registered));self.assertFalse(receipt['scientific_labels_changed']);self.assertEqual(receipt['automated'],6);self.assertEqual(receipt['manual'],14)


if __name__=='__main__':unittest.main()
