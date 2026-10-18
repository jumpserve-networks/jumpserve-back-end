"""Public HTTPS bytes pinned to validated addresses; extraction is not review."""
import datetime as dt
import http.client
import ipaddress
import json
import socket
import ssl
import tempfile
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlsplit
import intake
import store
from workflow import canonical,digest,now,public_url

MAX_BYTES=10_000_000
CHUNK_CHARACTERS=14000

def address(url):
 public_url(url)
 u=urlsplit(url)
 if u.scheme!='https' or u.port not in (None,443) or u.fragment or not u.hostname or u.hostname.endswith('.local'):
  raise ValueError('Paper retrieval requires a public HTTPS resource on port 443.')
 addresses=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
 if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
  raise ValueError('Private, local, reserved and metadata addresses cannot be retrieved.')
 return u,addresses[0][4][0]

class PinnedHTTPS(http.client.HTTPSConnection):
 def __init__(self,host,ip,timeout): super().__init__(host,timeout=timeout);self.pinned_ip=ip
 def connect(self):
  sock=socket.create_connection((self.pinned_ip,443),self.timeout)
  self.sock=self._context.wrap_socket(sock,server_hostname=self.host)

def fetch(url):
 started=time.monotonic();audit=[]
 for _ in range(6):
  u,ip=address(url); remaining=35-(time.monotonic()-started)
  if remaining<=0: raise ValueError('Paper retrieval wall-time limit reached.')
  connection=PinnedHTTPS(u.hostname,ip,min(10,remaining));chunks=[]
  try:
   connection.request('GET',(u.path or '/')+('?' +u.query if u.query else ''),headers={'User-Agent':'JumpServeResearch/1.0','Accept':'application/pdf,text/html','Accept-Encoding':'identity'})
   response=connection.getresponse()
   audit.append(dict(url=url,status_code=response.status,retrieved_at=now(),media_type=response.getheader('Content-Type')))
   if response.status in (301,302,303,307,308):
    location=response.getheader('Location')
    if not location: raise ValueError('Redirect omitted its destination.')
    url=urljoin(url,location);continue
   if response.status!=200: raise ValueError('Public full-text retrieval returned HTTP '+str(response.status)+'.')
   length=response.getheader('Content-Length')
   if length and int(length)>MAX_BYTES: raise ValueError('Paper exceeds the 10 MB original-byte limit.')
   chunks=[];size=0
   while size<=MAX_BYTES:
    remaining=35-(time.monotonic()-started)
    if remaining<=0: raise ValueError('Paper retrieval wall-time limit reached.')
    if connection.sock: connection.sock.settimeout(min(10,remaining))
    chunk=response.read1(min(65536,MAX_BYTES+1-size))
    if not chunk: break
    chunks.append(chunk);size+=len(chunk)
   raw=b''.join(chunks)
   if size>MAX_BYTES or length and size!=int(length): raise ValueError('Incomplete or oversized original; no truncated paper is treated as full text.')
   return raw,url,audit
  except Exception as error:
   error.retrieval_attempts=audit
   error.captured_response=b''.join(chunks)
   raise
  finally: connection.close()
 raise ValueError('Paper redirect limit reached.')

class PDFLink(HTMLParser):
 def __init__(self): super().__init__();self.url=None
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='meta' and a.get('name','').lower()=='citation_pdf_url':self.url=a.get('content')

def _original(study,actor):
 uid=lambda key:str(uuid.uuid5(uuid.UUID(study['id']),key))
 raw,url,audit=fetch(study['paper_url'])
 if not raw[:1024].lstrip().startswith(b'%PDF-'):
  landing=store.artifact(study['id'],actor,raw,uid('landing:'+digest(raw)),'text/html',dict(kind='paper-landing-original',requested_url=study['paper_url'],retrieved_url=url,transformation='none'))
  store.append(study['id'],actor,'artifacts',landing)
  parser=PDFLink();parser.feed(raw.decode('utf-8',errors='replace'))
  if not parser.url: raise ValueError('The public page did not identify an accessible PDF. A full-text resource or lawful original artifact is required.')
  raw,url,second=fetch(urljoin(url,parser.url));audit+=second
 if not raw[:1024].lstrip().startswith(b'%PDF-'):raise ValueError('Retrieved response is not a full-text PDF.')
 artifact=store.artifact(study['id'],actor,raw,uid('paper-original:'+digest(raw)),'application/pdf',dict(kind='paper-original',requested_url=study['paper_url'],retrieved_url=url,transformation='none',retrieval_attempts=audit))
 store.append(study['id'],actor,'artifacts',artifact)
 source=dict(id=uid('source:'+digest(raw)),citation=study['title'],source_url=study['paper_url'],retrieved_url=url,kind='main paper',role='Candidate claim extraction from accessible original bytes',retrieved_version='Public response retrieved '+now(),sha256=digest(raw),byte_count=len(raw),access_status='retrieved',review_status='retrieved-unreviewed',
  review_definition='Complete review requires every section, figure, table, appendix and available supplement. Text extraction and model processing do not establish complete review.',examined='Original-byte retrieval only; linked text-packet artifacts record any later extraction. Candidate extraction is not substantive review.',unexamined='Figures, equations, tables, image/scanned content and supplements have not been substantively examined.',retrieval_attempts=audit,
  reviewer=dict(identity='Autonomous source retrieval and text extractor',type='AI',independence='Programmatic acquisition; no independent or human substantive review'),findings='No scientific finding established by retrieval or extraction.',limitations='Candidate claim inventory covers available extracted text only. Missing text and omitted visual/supplement evidence remain uncovered.')
 store.append(study['id'],actor,'sources',source)
 with tempfile.TemporaryDirectory(prefix='jumpserve-paper-') as folder:
  path=Path(folder)/'original.pdf';out=Path(folder)/'text.json';path.write_bytes(raw)
  try:
   intake.text_packet(path,out);packet=out.read_bytes()
  except Exception:
   failure=Path(str(out)+'.failure.json')
   if failure.exists():
    data=failure.read_bytes();record=store.artifact(study['id'],actor,data,uid('text-failure:'+digest(data)),'application/json',dict(kind='failed-text-extraction',original_artifact_id=artifact['id'],transformation='Original extraction failure report; no successful text packet inferred'))
    store.append(study['id'],actor,'artifacts',record)
   raise
 text_artifact=store.artifact(study['id'],actor,packet,uid('paper-text:'+digest(packet)),'application/json',dict(kind='derived-pdf-text',original_artifact_id=artifact['id'],original_sha256=artifact['sha256'],transformation='intake.text_packet; pypdf; text extraction is not substantive review'))
 store.append(study['id'],actor,'artifacts',text_artifact)
 return dict(source_id=source['id'],text_artifact_id=text_artifact['id'],original_artifact_id=artifact['id'],chunk_index=0)

def original(study,actor):
 try:return _original(study,actor)
 except Exception as error:
  attempts=getattr(error,'retrieval_attempts',[])+[dict(requested_url=study['paper_url'],ended_at=now(),status='failed',reason=str(error) if isinstance(error,ValueError) else type(error).__name__)]
  captured=getattr(error,'captured_response',b'')
  if captured:
   record=store.artifact(study['id'],actor,captured,str(uuid.uuid4()),'application/octet-stream',dict(kind='failed-retrieval-response-prefix',transformation='none; possibly incomplete/invalid response, not full-paper bytes',retrieval_attempts=attempts))
   store.append(study['id'],actor,'artifacts',record)
  if not store.rows('sources',{'study_id':'eq.'+study['id'],'limit':1}):
   source=dict(id=str(uuid.uuid4()),citation=study['title'],source_url=study['paper_url'],retrieved_url=None,kind='main paper',role='Attempted original-paper retrieval',retrieved_version=None,sha256=None,byte_count=None,access_status='unavailable',review_status='unavailable-full-text',review_definition='Complete review requires legally available original bytes and substantive examination of every section, figure, table, appendix and supplement.',examined='No full paper examined',unexamined='All substantive paper content',retrieval_attempts=attempts,reviewer=dict(identity='Autonomous retrieval controller',type='AI',independence='No human or substantive source review'),findings='No scientific finding; required original bytes are unavailable.',limitations='A landing page, partial response or failed parse cannot establish complete paper access or review.')
   store.append(study['id'],actor,'sources',source)
  raise

def chunks(packet):
 result=[]
 for page in packet['pages']:
  content=page['text']
  if not content.strip(): continue
  for start in range(0,len(content),CHUNK_CHARACTERS):
   result.append(dict(page=page['page'],text=content[start:start+CHUNK_CHARACTERS],offset=start))
 return result

def candidates(study_id,source_id,chunk,values):
 entries=[]
 for value in values:
  if value['page']!=chunk['page'] or value['quote'] not in chunk['text']:
   raise ValueError('Candidate claim quote is absent from the exact supplied source page/chunk.')
  identity=source_id+':'+str(value['page'])+':'+value['quote']
  entries.append(dict(id=str(uuid.uuid5(uuid.UUID(study_id),identity)),source_id=source_id,location='PDF page '+str(value['page']),description=value['quote'],claim_type=value['claim_type'],scope=dict(origin='AI candidate extracted from original page text; substantive source review incomplete',source_page=value['page'],exact_quote=value['quote'],source_text_offset=chunk['offset']),metrics=[],priority=3))
 return entries
