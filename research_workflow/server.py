"""Local API entry point; uses the same Google authentication and Supabase store."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse
import json
import urllib.parse
from api import handler, MAX_BODY
import re

class Server(BaseHTTPRequestHandler):
    catalogue_only = False
    allowed_origin = 'http://localhost:6001'
    def handle_request(self):
        path=urllib.parse.urlsplit(self.path)
        query=dict(urllib.parse.parse_qsl(path.query))
        if self.catalogue_only and (self.command != 'GET' or not (path.path in ('/research/papers', '/research/prompts') or re.fullmatch(r'/research/(?:papers/[0-9a-f-]{36}(?:/title)?|prompts/[0-9a-f-]{36})',path.path) or path.path == '/research/studies' and query == {'mine':'1','status':'1'})):
            self.send_response(405);self.end_headers();return
        length=int(self.headers.get("Content-Length","0"))
        if length>MAX_BODY:
            self.send_response(413);self.end_headers();return
        event={"rawPath":path.path,"headers":dict(self.headers),"queryStringParameters":dict(urllib.parse.parse_qsl(path.query)),"requestContext":{"http":{"method":self.command}},"body":self.rfile.read(length).decode() if length else None}
        response=handler(event);self.send_response(response["statusCode"])
        for key,value in response["headers"].items():self.send_header(key,value)
        self.send_header("Access-Control-Allow-Origin",self.allowed_origin)
        self.end_headers();self.wfile.write(response["body"].encode())
    do_GET=handle_request
    do_POST=handle_request
    def do_OPTIONS(self):
        self.send_response(204);self.send_header("Access-Control-Allow-Origin",self.allowed_origin);self.send_header("Access-Control-Allow-Headers","Content-Type,Authorization");self.send_header("Access-Control-Allow-Methods","GET,OPTIONS" if self.catalogue_only else "GET,POST,OPTIONS");self.end_headers()
    def log_message(self,*args): pass  # No bearer headers or private record values.

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--port",type=int,default=4101);parser.add_argument('--catalogue-only',action='store_true');parser.add_argument('--origin',choices=['http://localhost:6001','http://localhost:3000'],default='http://localhost:6001');args=parser.parse_args()
    Server.catalogue_only=args.catalogue_only;Server.allowed_origin=args.origin
    print(json.dumps({"origin":f"http://localhost:{args.port}","authentication":"actual Supabase Google session; no test bypass"}),flush=True)
    ThreadingHTTPServer(("127.0.0.1",args.port),Server).serve_forever()
