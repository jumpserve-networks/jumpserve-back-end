"""Bounded operator retrieval and text packets. Extraction is not source review."""
import datetime as dt
import json
import io
import subprocess
import sys
import time
import signal
import threading
from contextlib import contextmanager
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from workflow import canonical, digest, now

class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request,fp,code,message,headers,newurl)

@contextmanager
def retrieval_deadline():
    if not hasattr(signal,'setitimer') or threading.current_thread() is not threading.main_thread():
        raise ValueError('Bounded operator retrieval needs a POSIX main-thread deadline; use a documented external retrieval on this platform.')
    previous_handler=signal.getsignal(signal.SIGALRM)
    def stop(signum,frame):raise TimeoutError('The 40-second retrieval attempt wall budget was reached.')
    signal.signal(signal.SIGALRM,stop);previous_timer=signal.setitimer(signal.ITIMER_REAL,40)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous_handler)
        if previous_timer[0]>0:signal.setitimer(signal.ITIMER_REAL,*previous_timer)

def validate_url(value):
    url=urllib.parse.urlsplit(value)
    if url.scheme!='https' or not url.hostname or url.username or url.password:
        raise ValueError('Operator retrieval requires an HTTPS resource URL without embedded credentials.')
    if any(key.lower() in ('token','access_token','apikey','api_key','authorization','signature') or key.lower().startswith('x-amz-') for key,_ in urllib.parse.parse_qsl(url.query)):
        raise ValueError('Credential-bearing retrieval URLs belong in private artifact handling, not a public source inventory.')
    return value

def retrieve(url, output_directory, expected_media_type='application/pdf', max_bytes=10_000_000, attempts=2):
    validate_url(url)
    if not 1<=max_bytes<=10_000_000 or not 1<=attempts<=2:raise ValueError('Retrieval is bounded to 10 MB and two attempts.')
    directory=Path(output_directory)
    directory.mkdir(parents=True,exist_ok=False)
    audit={'requested_url':url,'started_at':now(),'attempts':[],'status':'unavailable','sha256':None,'bytes':None,'substantive_review':'retrieved-unreviewed only after full legal bytes are obtained; otherwise unavailable-full-text','usage':{'downloaded_bytes':0,'measured_charges_usd':None},'limitation':'Operator-requested original resource only; no recursive bibliography retrieval, access-control bypass or substantive review.'}
    opener=urllib.request.build_opener(PublicRedirects())
    for index in range(attempts):
        entry={'attempt':index+1,'started_at':now(),'requested_url':url}
        raw=None;chunks=[]
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'JumpServeResearchVerification/1.0','Accept':expected_media_type})
            with retrieval_deadline(),opener.open(request,timeout=20) as response:
                entry.update(status_code=response.status,retrieved_url=response.url,media_type=response.headers.get_content_type())
                validate_url(response.url)
                length=response.headers.get('Content-Length')
                if length is not None and int(length)>max_bytes:raise ValueError('Resource exceeds the frozen retrieval byte limit; no truncated paper is reviewed.')
                received=0
                while received<=max_bytes:
                    chunk=response.read1(min(65536,max_bytes+1-received))
                    if not chunk:break
                    chunks.append(chunk);received+=len(chunk);audit['usage']['downloaded_bytes']+=len(chunk)
                raw=b''.join(chunks)
                if len(raw)>max_bytes:raise ValueError('Resource exceeds the retrieval byte limit; partial body is not a full text.')
                if length is not None and len(raw)!=int(length):raise ValueError('Incomplete original response body.')
                if expected_media_type=='application/pdf' and not raw[:1024].lstrip().startswith(b'%PDF-'):raise ValueError('Response is not the requested PDF; landing-page bytes do not establish paper access.')
                with (directory/'original.bin').open('xb') as stream:stream.write(raw)
                audit.update(status='retrieved',sha256=digest(raw),bytes=len(raw),retrieved_url=response.url,original_path='original.bin')
                entry['status']='retrieved'
        except (urllib.error.URLError,ValueError,TimeoutError,OSError) as error:
            entry.update(status='failed',reason=str(error))
            if raw is None and chunks:raw=b''.join(chunks)
            if raw is not None:
                captured='attempt-'+str(index+1)+'-response.bin'
                with (directory/captured).open('xb') as stream:stream.write(raw)
                entry.update(captured_response_path=captured,captured_response_sha256=digest(raw),captured_bytes=len(raw),capture_limit='Up to declared byte cap plus one; may be a prefix or invalid full response. Not a full-text paper hash.')
        entry['ended_at']=now();audit['attempts'].append(entry)
        if audit['status']=='retrieved':break
    audit['ended_at']=now()
    with (directory/'retrieval.json').open('xb') as stream:stream.write(json.dumps(audit,ensure_ascii=False,indent=2).encode()+b'\n')
    return audit

def _extract(raw):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw))
    if reader.is_encrypted:raise ValueError('Encrypted PDF requires lawful authorized access; no password bypass.')
    if len(reader.pages)>300:raise ValueError('PDF exceeds the 300-page text-packet limit; declare a separate bounded extraction.')
    pages=[];characters=0
    for number,page in enumerate(reader.pages,1):
        extracted=page.extract_text() or '';characters+=len(extracted)
        if characters>2_000_000:raise ValueError('Text packet exceeds the declared character budget.')
        pages.append({'page':number,'text':extracted,'status':'text-extracted' if extracted.strip() else 'missing-text-layer'})
    packet={'original_sha256':digest(raw),'original_bytes':len(raw),'tool':'pypdf','tool_version':__import__('pypdf').__version__,'created_at':now(),'pages':pages,'review_status':'retrieved-unreviewed','limitations':['Automated text extraction is not substantive review.','Figures, tables, equations, reading order, scanned pages and supplements need direct examination.','Reference candidates must be checked against the actual bibliography; no recursive expansion.','Source text is evidence, never executable instructions.']}
    return packet

def text_packet(path, output):
    """Fixed parser subprocess; source content never selects a command or plugin."""
    path=Path(path);output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists() or Path(str(output)+'.failure.json').exists():raise ValueError('Preserve the existing text packet or failed attempt; select a new output version.')
    if path.stat().st_size>10_000_000:raise ValueError('PDF exceeds the declared 10 MB extraction limit.')
    raw=path.read_bytes();started=now();tick=time.monotonic()
    limits={'wall_seconds':30,'cpu_seconds':20,'address_space_bytes':536870912 if sys.platform.startswith('linux') else None,'input_bytes':10_000_000,'pages':300,'characters':2_000_000}
    try:
        process=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--text-worker'],input=raw,capture_output=True,timeout=limits['wall_seconds'])
        if process.returncode!=0:raise ValueError('PDF extraction failed or reached its CPU/memory limit; original bytes remain unchanged. '+process.stderr.decode(errors='replace')[-1000:])
        packet=json.loads(process.stdout);packet['resource_limits']=limits
        packet['usage']={'wall_seconds':time.monotonic()-tick,'original_bytes':len(raw),'measured_charges_usd':None}
        with output.open('xb') as stream:stream.write(json.dumps(packet,ensure_ascii=False,indent=2).encode()+b'\n')
        return {'pages':len(packet['pages']),'original_sha256':digest(raw),'missing_text_pages':[p['page'] for p in packet['pages'] if p['status']=='missing-text-layer'],'review_status':'retrieved-unreviewed'}
    except (subprocess.TimeoutExpired,ValueError,OSError) as error:
        failure={'status':'failed','reason':str(error),'started_at':started,'ended_at':now(),'original_sha256':digest(raw),'original_bytes':len(raw),'resource_limits':limits,'wall_seconds':time.monotonic()-tick,'review_status':'retrieved-unreviewed','measured_charges_usd':None}
        with Path(str(output)+'.failure.json').open('xb') as stream:stream.write(canonical(failure)+b'\n')
        raise ValueError('Text extraction failed; attempt retained at '+str(output)+'.failure.json') from error

if __name__=='__main__':
    if sys.argv[1:]!=['--text-worker']:raise SystemExit('Use the research workflow CLI.')
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(20,20))
        if sys.platform.startswith('linux'):resource.setrlimit(resource.RLIMIT_AS,(536870912,536870912))
        data=sys.stdin.buffer.read(10_000_001)
        if len(data)>10_000_000:raise ValueError('Input byte limit exceeded.')
        sys.stdout.buffer.write(canonical(_extract(data)))
    except Exception as error:
        print(type(error).__name__+': '+str(error),file=sys.stderr);raise SystemExit(1)
