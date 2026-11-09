"""Retrieve the directly cited bibliography only; downloads never imply review."""
import concurrent.futures
import datetime
import hashlib
import json
from pathlib import Path
import re
import ssl
import urllib.request
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / 'sources'
RESEARCH = {1,5,6,7,9,12,17,18,20,21,22,24,25,26,27,28,29,30,32,33,34,36,37,41,42,43,44,47,48}
STANDARDS = {2,3,8,10,11,15,16,38,45}
URLS = {
    9: ['https://www.usenix.org/system/files/sec21-bock.pdf'],
    18: ['https://www.blackhat.com/docs/us-17/wednesday/us-17-Gil-Web-Cache-Deception-Attack-wp.pdf'],
    21: ['https://www.cgisecurity.com/lib/HTTP-Request-Smuggling.pdf'],
    25: ['https://www.usenix.org/system/files/sec22-jabiyev.pdf'],
    27: ['https://arxiv.org/pdf/2405.17737'],
    32: ['https://www.usenix.org/legacy/events/usits01/full_papers/krishnamurthy/krishnamurthy.pdf'],
    33: ['https://www.usenix.org/system/files/sec20-mirheidari.pdf'],
    34: ['https://www.usenix.org/system/files/sec22-mirheidari.pdf'],
    42: ['https://web.mit.edu/Saltzer/www/publications/endtoend/endtoend.pdf']
}
CONTEXT = ssl.create_default_context(cafile='/etc/ssl/cert.pem')

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def download(url):
    req = urllib.request.Request(url, headers={'User-Agent':'JumpServe research source retrieval (no access-control bypass)'})
    with urllib.request.urlopen(req, timeout=35, context=CONTEXT) as r:
        return r.read(), r.url, r.status, r.headers.get('Content-Type')

def retrieve(row):
    n = row['reference_number']
    prior = row.setdefault('retrieval_audit', [])
    doi = row.get('doi')
    if doi and not row.get('metadata_resolved'):
        try:
            raw, _, _, _ = download('https://api.crossref.org/works/'+doi)
            meta = json.loads(raw)['message']
            (SOURCES / f'ref-{n:02}-metadata.json').write_bytes(raw)
            row['title'] = meta.get('title', [''])[0]
            row['citation'] = row.get('citation') or '; '.join(' '.join(filter(None,[a.get('given'),a.get('family')])) for a in meta.get('author',[])) + '. ' + str(meta.get('published',{}).get('date-parts',[['']])[0][0])+'. '+row['title']+'. DOI: '+doi
            row['metadata_resolved'] = True
            row['metadata_sha256'] = hashlib.sha256(raw).hexdigest()
            row['publisher_links'] = meta.get('link',[])
        except Exception as e:
            prior.append({'url':'https://api.crossref.org/works/'+doi, 'at':now(), 'status':'metadata failed', 'reason':str(e)[:300]})
    urls = row.get('candidate_urls', URLS.get(n, []))
    if n in STANDARDS and doi and 'RFC' in doi:
        urls = ['https://www.rfc-editor.org/rfc/'+doi.split('/')[-1].lower()+'.txt']
    urls += [x['URL'] for x in row.get('publisher_links',[]) if 'pdf' in x.get('URL','') and x['URL'] not in urls]
    row['source_url'] = row.get('source_url') or ('https://doi.org/'+doi if doi else next(iter(re.findall(r'https?://[^\s]+',row.get('citation',''))),None))
    path = SOURCES / f'ref-{n:02}.pdf'
    txt = SOURCES / f'ref-{n:02}.txt'
    for url in urls:
        if path.exists() or (txt.exists() and n in STANDARDS): break
        try:
            data, final, code, content_type = download(url)
            event={'url':url,'final_url':final,'at':now(),'http_status':code,'content_type':content_type,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
            if data.startswith(b'%PDF-'):
                path.write_bytes(data)
                row['retrieved_url']=final
                row['retrieved_version']=row.get('version_note','Publisher/author PDF; exact version requires identity review')
                event['status']='downloaded PDF, pending identity review'
            elif n in STANDARDS and b'Request for Comments:' in data[:3000] and b'<html' not in data[:500].lower():
                txt.write_bytes(data)
                row['retrieved_url']=final
                row['retrieved_version']='RFC Editor text'
                event['status']='downloaded standard'
            else:
                event['status']='not full text: non-PDF response'
            prior.append(event)
        except Exception as e:
            prior.append({'url':url,'at':now(),'status':'failed','reason':str(e)[:300]})
    if path.exists():
        try:
            pdf=PdfReader(path)
            txt.write_text('\n\f\n'.join(p.extract_text() or '' for p in pdf.pages))
            row.update(download_status='downloaded',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),pages=len(pdf.pages),text_characters=len(txt.read_text()))
        except Exception as e:
            row.update(download_status='invalid PDF',reason=str(e))
    elif txt.exists():
        row.update(download_status='downloaded',sha256=hashlib.sha256(txt.read_bytes()).hexdigest())
    else:
        row.update(download_status='unavailable' if row['kind'] in ('research','standard') else 'citation only',sha256=None,retrieved_version=None)
    row.setdefault('review_status','unreviewed')
    row.setdefault('findings',None)
    row.setdefault('limitations','Download status is not review status; source identity and exact version require inspection.')
    return row

def main():
    SOURCES.mkdir(exist_ok=True)
    inventory=ROOT/'literature.json'
    if inventory.exists():
        rows=json.loads(inventory.read_text())
    else:
        metadata=json.loads((SOURCES/'paper-crossref.json').read_text())['message']
        rows=[{'reference_number':0,'citation':'Mahmoud Attia, Ilies Benhabbour, Marc Dacier. 2025. The Developer, the RFC, and the Middlebox: An HTTP/2 Compliance Story. IMC 2025, 258–273.','doi':'10.1145/3730567.3764447','kind':'main paper','source_url':'https://dl.acm.org/doi/10.1145/3730567.3764447','download_status':'unavailable','review_status':'partial','retrieved_version':None,'sha256':None,'findings':'Official program abstract and indexed KAUST excerpts reviewed. 156 tests, 12 local proxy implementations and three cloud providers are reported. Full text, figures, tables and appendices have not been reviewed.','limitations':'ACM Cloudflare challenge; KAUST Imperva/hCaptcha challenge. HTML returned as HTTP 200 was rejected as a PDF. Accepted-manuscript label appears in indexed KAUST front matter; no local manuscript hash is available.','retrieval_audit':[{'url':'https://dl.acm.org/doi/pdf/10.1145/3730567.3764447','status':'403/security challenge','at':now()},{'url':'https://repository.kaust.edu.sa/bitstreams/c6d78bf8-ee56-458c-a858-a8d611b51a5d/download','status':'200 HTML security challenge, rejected','at':now()}]}]
        for n,ref in enumerate(metadata['reference'],1):
            rows.append({'reference_number':n,'citation':ref.get('unstructured'),'doi':ref.get('DOI'),'kind':'research' if n in RESEARCH else 'standard' if n in STANDARDS else 'software or web source','source_url':None,'download_status':'pending','review_status':'unreviewed','sha256':None})
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        work=list(pool.map(retrieve, rows[1:]))
    inventory.write_text(json.dumps([rows[0],*work],indent=2)+'\n')
    print(json.dumps({'direct_references':len(work),'downloaded':[r['reference_number'] for r in work if r['download_status']=='downloaded'],'research_unavailable':[r['reference_number'] for r in work if r['kind']=='research' and r['download_status']=='unavailable']},indent=2))

if __name__ == '__main__': main()
