"""Local API entry point; uses the same Google authentication and Supabase store."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse
import json
import urllib.parse
from api import handler, MAX_BODY

class Server(BaseHTTPRequestHandler):
    def handle_request(self):
        path=urllib.parse.urlsplit(self.path)
        length=int(self.headers.get("Content-Length","0"))
        if length>MAX_BODY:
            self.send_response(413);self.end_headers();return
        event={"rawPath":path.path,"headers":dict(self.headers),"queryStringParameters":dict(urllib.parse.parse_qsl(path.query)),"requestContext":{"http":{"method":self.command}},"body":self.rfile.read(length).decode() if length else None}
        response=handler(event);self.send_response(response["statusCode"])
        for key,value in response["headers"].items():self.send_header(key,value)
        self.send_header("Access-Control-Allow-Origin","http://localhost:3000")
        self.end_headers();self.wfile.write(response["body"].encode())
    do_GET=handle_request
    do_POST=handle_request
    def do_OPTIONS(self):
        self.send_response(204);self.send_header("Access-Control-Allow-Origin","http://localhost:3000");self.send_header("Access-Control-Allow-Headers","Content-Type,Authorization");self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS");self.end_headers()
    def log_message(self,*args): pass  # No bearer headers or private record values.

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--port",type=int,default=4101);args=parser.parse_args()
    print(json.dumps({"origin":f"http://localhost:{args.port}","authentication":"actual Supabase Google session; no test bypass"}),flush=True)
    ThreadingHTTPServer(("127.0.0.1",args.port),Server).serve_forever()
