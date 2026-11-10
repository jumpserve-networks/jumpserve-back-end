"""Local server entrypoint with network/writes intercepted."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server


class LocalEntrypointBoundaries(unittest.TestCase):
    def test_local_server_honors_explicit_catalogue_origin_and_port_without_opening_a_socket(self):
        output=io.StringIO();listener=Mock()
        with patch.object(sys,'argv',['server.py','--catalogue-only','--origin','http://localhost:3000','--port','4102']),patch('http.server.ThreadingHTTPServer',return_value=listener) as create,contextlib.redirect_stdout(output):runpy.run_path(server.__file__,run_name='__main__')
        self.assertEqual(create.call_args.args[0],('127.0.0.1',4102));handler=create.call_args.args[1];self.assertTrue(handler.catalogue_only);self.assertEqual(handler.allowed_origin,'http://localhost:3000');listener.serve_forever.assert_called_once();self.assertEqual(json.loads(output.getvalue())['authentication'],'actual Supabase Google session; no test bypass')
        instance=object.__new__(server.Server);instance.path='/research/papers';instance.command='GET';instance.headers={'Content-Length':str(server.MAX_BODY+1)};instance.send_response=Mock();instance.end_headers=Mock()
        with patch.object(server,'handler') as dispatch:instance.handle_request()
        instance.send_response.assert_called_once_with(413);dispatch.assert_not_called()


if __name__=='__main__':unittest.main()
