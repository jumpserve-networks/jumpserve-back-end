"""The local catalogue server reads real APIs without enabling local writes."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

class CatalogueServer(unittest.TestCase):
    def test_catalogue_mode_rejects_writes_and_private_routes_before_dispatch(self):
        class ReadOnly(server.Server): catalogue_only=True
        with patch.object(server,'handler',return_value={'statusCode':200,'headers':{'Content-Type':'application/json'},'body':'{}'}) as handler:
            http=ThreadingHTTPServer(('127.0.0.1',0),ReadOnly)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                base='http://127.0.0.1:'+str(http.server_port)
                for path in ['/research/papers?initiator=1','/research/papers/11111111-1111-4111-8111-111111111111','/research/studies?mine=1&status=1','/research/prompts','/research/prompts/11111111-1111-4111-8111-111111111111']:
                    with urllib.request.urlopen(base+path) as response:
                        self.assertEqual(response.status,200)
                        self.assertEqual(response.headers['Access-Control-Allow-Origin'],'http://localhost:6001')
                self.assertEqual(handler.call_count,5);handler.reset_mock()
                for path,method in [('/research/papers','POST'),('/research/studies','POST'),('/research/studies','GET'),('/auth/v1/admin/users/anything','GET')]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(base+path,method=method))
                    self.assertEqual(error.exception.code,405)
                handler.assert_not_called()
                request=urllib.request.Request(base+'/research/papers',method='OPTIONS')
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.headers['Access-Control-Allow-Methods'],'GET,OPTIONS')
            finally:
                http.shutdown();http.server_close();thread.join()

if __name__=='__main__': unittest.main()
