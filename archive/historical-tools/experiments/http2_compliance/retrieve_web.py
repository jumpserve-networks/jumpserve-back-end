"""Retrieve cited public web originals, preserving HTML bytes and extraction limits."""
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import ssl
import urllib.request
import datetime
ROOT=Path(__file__).resolve().parent
URLS={20:'https://queue.acm.org/detail.cfm?id=2555617',28:'https://portswigger.net/research/http-desync-attacks-request-smuggling-reborn',29:'https://portswigger.net/research/http2',48:'https://www.w3.org/2001/01/qa-ws/pp/alex-rousskov-measfact',4:'https://raw.githubusercontent.com/python-hyper/h2/master/README.rst',13:'https://www.linode.com/legal-aup/',14:'https://raw.githubusercontent.com/drk1wi/Modlishka/master/README.md',19:'https://raw.githubusercontent.com/kgretzky/evilginx2/master/README.md',23:'https://raw.githubusercontent.com/summerwind/h2spec/master/README.md',31:'https://blog.cloudflare.com/hpack-the-silent-killer-feature-of-http-2/',35:'https://mitmproxy.org/',39:'https://radar.cloudflare.com/adoption-and-usage',40:'https://radar.cloudflare.com/',46:'https://raw.githubusercontent.com/muraenateam/muraena/master/README.md'}
class Extract(HTMLParser):
    def __init__(self): super().__init__();self.skip=0;self.text=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip and data.strip():self.text.append(data.strip())
def main():
    rows=json.loads((ROOT/'literature.json').read_text())
    for row in rows:
        n=row['reference_number']
        if n not in URLS:continue
        url=URLS[n];audit={'url':url,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'JumpServe research retrieval'})
            with urllib.request.urlopen(req,timeout=30,context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')) as r:raw=r.read();final=r.url;ctype=r.headers.get('Content-Type','')
            text=raw.decode('utf-8',errors='replace')
            if 'html' in ctype:
                e=Extract();e.feed(text);text='\n'.join(e.text)
            if len(text)<500 or any(x in text[:300].lower() for x in ('just a moment','access denied','captcha')):raise ValueError('Challenge, minimal page or inaccessible original')
            path=ROOT/'sources'/f'ref-{n:02}.web';path.write_bytes(raw)
            (ROOT/'sources'/f'ref-{n:02}.txt').write_text(text)
            sha=hashlib.sha256(raw).hexdigest();audit.update(status='downloaded web original; identity review required',sha256=sha,bytes=len(raw),final_url=final)
            row.update(download_status='downloaded',sha256=sha,retrieved_url=final,retrieved_version='Public web original retrieved 2026-10-04; current revision, not necessarily cited-date snapshot',text_characters=len(text))
        except Exception as error:audit.update(status='failed',reason=str(error))
        row.setdefault('retrieval_audit',[]).append(audit)
    (ROOT/'literature.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps({r['reference_number']:r['download_status'] for r in rows if r['reference_number'] in URLS}))
if __name__=='__main__':main()
