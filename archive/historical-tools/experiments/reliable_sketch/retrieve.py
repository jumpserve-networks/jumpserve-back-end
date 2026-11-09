"""Direct bibliography retrieval only. Preserve bytes; do not equate download with review."""
import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import ssl
import urllib.request
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / 'sources'
CTX = ssl.create_default_context(cafile='/etc/ssl/cert.pem')
URLS = {
 1: ['https://raw.githubusercontent.com/aappleby/smhasher/master/src/MurmurHash3.cpp'],
 2: ['https://api.github.com/repos/ReliableSketch/ReliableSketch/commits/5a83f03c775142401d23a78e7e81b163ddf7b604'],
 3: ['https://fimi.uantwerpen.be/data/','http://fimi.ua.ac.be/data/'],
 4: ['https://www.caida.org/catalog/datasets/passive_dataset/','https://www.caida.org/data/overview/'],
 5: ['https://www.usenix.org/system/files/nsdi22-agarwal.pdf'],
 7: ['https://www.tau.ac.il/~nogaa/PDFS/amsz4.pdf'],
 8: ['https://arxiv.org/pdf/1611.04825'],
 9: ['https://arxiv.org/pdf/210 counters'],
 12: ['https://www.cs.utexas.edu/~moore/best-ideas/mjrty/index.html'],
 14: ['https://www.cs.princeton.edu/courses/archive/spring04/cos598B/bib/CharikarCF.pdf'],
 16: ['https://www.vldb.org/conf/2005/papers/p13-cormode.pdf'],
 17: ['https://dimacs.rutgers.edu/~graham/pubs/papers/cm-full.pdf'],
 18: ['https://erikdemaine.org/papers/NetworkStats_ESA2002/paper.pdf'],
 19: ['https://cseweb.ucsd.edu/~varghese/PAPERS/sigcomm2002.pdf'],
 20: ['https://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf'],
 21: ['https://www.cs.cmu.edu/~zaoxing/papers/sketchvisor.pdf'],
 22: ['https://www.usenix.org/system/files/nsdi21-huang.pdf'],
 24: ['https://arxiv.org/pdf/1603.05346'],
 25: ['https://www.vldb.org/pvldb/vol15/p1426-li.pdf'],
 28: ['https://www.vldb.org/pvldb/vol12/p2195-masson.pdf','https://arxiv.org/pdf/1908.10693'],
 29: ['https://www.vldb.org/pvldb/vol16/p3796-melissourgos.pdf'],
 30: ['https://www.cs.ucsb.edu/research/tech_reports/reports/2005-23.pdf'],
 31: ['https://www.cs.utexas.edu/~moore/best-ideas/mjrty/index.html'],
 32: ['https://www.usenix.org/system/files/nsdi22-namkung.pdf'],
 33: ['https://www.web-polygraph.org/docs/papers/polygraph-spe.pdf','https://www.web-polygraph.org/'],
 35: ['https://arxiv.org/pdf/1611.04825'],
 36: ['https://arxiv.org/pdf/1910.026 MV'],
 40: ['https://yangtonghome.github.io/uploads/ElasticSketch.pdf'],
 43: ['https://arxiv.org/pdf/2112.03462'],
 44: ['https://www.vldb.org/pvldb/vol16/p1291-zhao.pdf'],
 45: ['https://www.vldb.org/pvldb/vol14/p1215-zhao.pdf'],
}
# Remove placeholder candidate guesses; unresolved entries use recorded DOI URLs.
URLS.pop(9); URLS.pop(36); URLS.pop(8)  # HashPipe PDF belongs to ref35, not ref8.
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(raw): return hashlib.sha256(raw).hexdigest()
def fetch(url):
 req=urllib.request.Request(url,headers={'User-Agent':'JumpServe source audit/1.0'})
 with urllib.request.urlopen(req,timeout=25,context=CTX) as r: return r.read(),r.url,r.status,r.headers.get('Content-Type','')
def retrieve(row):
 n=row['reference_number']; audit=row.setdefault('retrieval_attempts',[])
 candidates=list(URLS.get(n,[])); doi=row.get('doi')
 if doi:
  try:
   raw,url,code,typ=fetch('https://api.crossref.org/works/'+doi)
   path=SOURCES/f'ref-{n:02}-metadata.json';path.write_bytes(raw)
   meta=json.loads(raw)['message'];row['metadata_sha256']=digest(raw)
   audit.append(dict(url=url,at=now(),status='metadata only',http_status=code,sha256=digest(raw),bytes=len(raw)))
   candidates.extend(x['URL'] for x in meta.get('link',[]) if 'pdf' in x.get('URL','').lower())
  except Exception as exc: audit.append(dict(url='https://api.crossref.org/works/'+doi,at=now(),status='failed',reason=str(exc)[:180]))
  candidates.append('https://doi.org/'+doi)
 if not candidates:
  candidates=[row['source_url']] if row.get('source_url') else []
 for url in dict.fromkeys(candidates):
  try:
   raw,final,code,typ=fetch(url); event=dict(url=url,final_url=final,at=now(),http_status=code,content_type=typ,sha256=digest(raw),bytes=len(raw))
   pdf=raw.startswith(b'%PDF-'); original=n in {1,2,3,4,12,31,33}
   if pdf or original:
    ext='pdf' if pdf else 'cpp' if n==1 else 'json' if n==2 else 'html'
    path=SOURCES/f'ref-{n:02}.{ext}';path.write_bytes(raw)
    row.update(access_status='retrieved',retrieved_url=final,original_path=str(path.relative_to(ROOT)),sha256=digest(raw),byte_count=len(raw),retrieved_at=now(),retrieved_version='exact retained bytes; identity/version pending inspection',review_status='unreviewed')
    if pdf:
     reader=PdfReader(path);row['pages']=len(reader.pages)
     path.with_suffix('.txt').write_text('\n\f\n'.join(p.extract_text() or '' for p in reader.pages))
    event['status']='retrieved; identity pending';audit.append(event);break
   event['status']='landing page; full text unavailable';audit.append(event)
  except Exception as exc: audit.append(dict(url=url,at=now(),status='failed',reason=str(exc)[:180]))
 if row['access_status']!='retrieved':row.update(access_status='unavailable',review_status='unavailable',limitations='Full text unavailable after the recorded bounded attempts; citation/metadata only, no findings inferred.')
 return row
def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--retry-unavailable',action='store_true');parser.add_argument('--final-pass',action='store_true');args=parser.parse_args()
 if args.final_pass:
  rows=json.loads((ROOT/'literature.json').read_text());bad=next(r for r in rows if r['reference_number']==36)
  if bad.get('retrieved_url')=='https://arxiv.org/pdf/1902.00878':
   for ext in ('pdf','txt'):
    source=SOURCES/f'ref-36.{ext}'
    if source.exists():source.rename(SOURCES/f'rejected-ref-36-wrong-identity.{ext}')
   bad['retrieval_attempts'][-1]['identity_status']='rejected: smart-contract PKI paper; wrong identity'
   bad.update(access_status='unavailable',review_status='unavailable',sha256=None,original_path=None,retrieved_url=None,retrieved_version=None,pages=None)
  URLS.update({13:['https://zte.magtechjournal.com/CN/article/downloadArticleFile.do?attachType=PDF&id=814'],17:['https://www.cs.ox.ac.uk/people/graham.cormode/pubs/papers/cm-full.pdf'],23:['https://www.zte.com.cn/content/dam/zte-site/res-www-zte-com-cn/mediares/magazine/publication/com_en/pdf/en202303-.pdf'],29:['https://www.vldb.org/pvldb/vol16/p4296-wang.pdf'],30:['https://www.cs.emory.edu/~cheung/Courses/584/Syllabus/papers/Frequency-count/2005-Metwally-Top-k-elements.pdf'],34:['https://discovery.ucl.ac.uk/id/eprint/10188361/1/Tailatency_Sketch_SIGMOD__for_RPS_.pdf'],36:['https://www.cse.cuhk.edu.hk/~pclee/www/pubs/infocom19mvsketch.pdf','https://arxiv.org/pdf/1910.10441'],37:['https://arxiv.org/pdf/1709.04048'],15:['https://www.cs.ox.ac.uk/people/graham.cormode/pubs/papers/sketch.pdf'],6:['https://www.tau.ac.il/~nogaa/PDFS/join.pdf'],12:['https://www.cs.utexas.edu/~moore/publications/mjrty.pdf']})
  with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:updated=list(pool.map(retrieve,[r for r in rows if r['access_status']=='unavailable']))
  by_id={r['reference_number']:r for r in updated};rows=[by_id.get(r['reference_number'],r) for r in rows]
  for r in rows:
   if not r['retrieval_attempts']:r['retrieval_attempts'].append(dict(at=now(),status='citation catalog inspected; original full-text URL unresolved',reason='No DOI or legally accessible resource identified in bounded search; not a download failure'))
  (ROOT/'literature.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps({'retrieved':sum(r['access_status']=='retrieved' for r in rows),'attempted':len(updated)}));return
 if args.retry_unavailable:
  URLS.update({3:['https://fimi.uantwerpen.be/data/','https://fimi.ua.ac.be/data/'],5:['https://www.usenix.org/system/files/nsdi22-paper-agarwal.pdf'],8:['https://engineering.purdue.edu/~xiaoqic/documents/paper-PRECISION-ToN.pdf'],9:['https://arxiv.org/pdf/2102.12531','https://discovery.ucl.ac.uk/10125499/1/SALSA_UCL.pdf'],10:['https://arxiv.org/pdf/1808.03412'],11:['https://pages.cs.wisc.edu/~akella/papers/imc10.pdf','https://tbenson.github.io/docs/imc10.pdf'],15:['https://www.cs.ox.ac.uk/people/graham.cormode/papers/cormode.pdf'],17:['https://archive.dimacs.rutgers.edu/~graham/pubs/papers/cm-full.pdf','https://www.cs.ox.ac.uk/people/graham.cormode/papers/cm-full.pdf'],19:['https://pages.cs.wisc.edu/~estan/publications/measurement.pdf'],21:['https://qhuang.me/papers/sketchvisor_sigcomm17.pdf'],26:['https://arxiv.org/pdf/1908.042 Nitro'],27:['https://www.cs.cmu.edu/~zaoxing/papers/univmon.pdf'],29:['https://www.vldb.org/pvldb/vol16/p3790-melissourgos.pdf'],30:['https://sites.cs.ucsb.edu/research/tech_reports/reports/2005-23.pdf'],32:['https://www.usenix.org/system/files/nsdi22-paper-namkung.pdf'],36:['https://arxiv.org/pdf/1902.00878'],40:['https://yangtonghome.github.io/uploads/elastic.pdf','https://yangzhou1997.github.io/paper/elastic-sigcomm18.pdf'],41:['https://yangtonghome.github.io/uploads/CocoSketch.pdf']})
  URLS.pop(26)
  rows=json.loads((ROOT/'literature.json').read_text())
  with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
   updated=list(pool.map(retrieve,[r for r in rows if r['access_status']=='unavailable']))
  by_id={r['reference_number']:r for r in updated};rows=[by_id.get(r['reference_number'],r) for r in rows]
  (ROOT/'literature.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps({'retrieved':sum(r['access_status']=='retrieved' for r in rows),'attempted':len(updated)}));return
 SOURCES.mkdir(exist_ok=True)
 raw,url,code,typ=fetch('https://api.crossref.org/works/10.1145/3730567.3764459')
 (SOURCES/'main-crossref.json').write_bytes(raw);meta=json.loads(raw)['message']
 text=PdfReader(SOURCES/'main.pdf').pages[13].extract_text();parts=re.split(r'\[(\d+)\]\s+',text)[1:]
 citations={int(parts[i]):' '.join(parts[i+1].strip().split()) for i in range(0,len(parts),2)}
 assert set(citations)==set(range(1,46))
 refs=meta.get('reference',[]);by_number={}
 for i,ref in enumerate(refs,1):
  key=ref.get('key','');match=re.search(r'(\d+)$',key)
  by_number[i]=ref
 rows=[]
 for n,citation in citations.items():
  ref=by_number.get(n,{})
  doi=ref.get('DOI');url=('https://doi.org/'+doi) if doi else next(iter(re.findall(r'https?://[^\s]+',citation)),None)
  rows.append(dict(reference_number=n,citation=citation,doi=doi,kind='software' if n in {1,2} else 'dataset' if n in {3,4} else 'research',role='implementation' if n in {1,2} else 'input provenance' if n in {3,4,11,33} else 'baseline' if n in {10,17,19,30,35,40,41} else 'related work/theory',source_url=url,access_status='pending',review_status='unreviewed',retrieved_version=None,sha256=None,pages=None,findings=None,limitations='Retrieval does not establish identity, exact revision or review completeness.',retrieval_attempts=[]))
 paper=SOURCES/'main.pdf'
 main=dict(reference_number=0,citation='Wu et al. 2025. Approaching 100% Confidence in Stream Summary through ReliableSketch. IMC 2025, 18 pages.',doi='10.1145/3730567.3764459',kind='main paper',role='assessed paper',source_url='https://yangtonghome.github.io/uploads/ReliableSketch__IMC_2025.pdf',retrieved_url='https://yangtonghome.github.io/uploads/ReliableSketch__IMC_2025.pdf',access_status='retrieved',review_status='partial',retrieved_version='Author-hosted IMC 2025 PDF, 18 pages, DOI 10.1145/3730567.3764459; retrieved '+now(),original_path='sources/main.pdf',sha256=digest(paper.read_bytes()),byte_count=paper.stat().st_size,pages=18,retrieved_at=now(),findings='Stream summary for positive weights. Empirical default excludes auxiliary fallback; formal bound includes it. CPU, FPGA and Tofino claims have different conditions.',limitations='Main text read; proof audit and numerical digitization of all curves incomplete. Appendix concentration steps require further independent mathematical review.',retrieval_attempts=[dict(url='https://yangtonghome.github.io/uploads/ReliableSketch__IMC_2025.pdf',status='retrieved PDF by curl',at=now(),sha256=digest(paper.read_bytes()))])
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool: rows=list(pool.map(retrieve,rows))
 (ROOT/'literature.json').write_text(json.dumps([main,*rows],indent=2)+'\n')
 print(json.dumps({'sources':46,'retrieved':sum(r['access_status']=='retrieved' for r in [main,*rows]),'main_sha256':main['sha256']}))
if __name__=='__main__': main()
