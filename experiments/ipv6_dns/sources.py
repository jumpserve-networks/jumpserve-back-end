"""Bounded direct-reference retrieval, original-byte retention, no recursive bibliography expansion."""
import concurrent.futures as cf,datetime as dt,difflib,hashlib,html,json,re,ssl,sys,urllib.parse,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
URLS={1:'https://labs.apnic.net/measurements/',3:'https://archive.nanog.org/meetings/nanog32/presentations/kosters.pdf',13:'https://icir.org/mallman/pubs/CAZ%2B14/CAZ%2B14.pdf',14:'https://arxiv.org/pdf/2205.06085v1',20:'https://www.dns-oarc.net/index.php/oarc/data/ditl',21:'https://www.youtube.com/watch?v=7qJ9eg4UREk',25:'https://web.archive.org/web/20250206161534/https://insinuator.net/2015/11/some-notes-on-the-drop-ipv6-fragments-vs-this-will-break-dnssec-debate/',28:'https://pure.mpg.de/rest/items/item_3517635_5/component/file_3561636/content',31:'https://publicsuffix.org/',32:'https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-119.pdf',39:'https://developer.chrome.com/docs/crux',40:'https://pure.mpg.de/rest/items/item_3636923/component/file_3636924/content',42:'https://www.dotnxdomain.net/ispcol/2017-08/xtn-hdrs.html',43:'https://www.potaroo.net/ispcol/2023-11/dns-ipv6.html',44:'https://www.potaroo.net/ispcol/2024-02/truncation.html',45:'https://www.iana.org/assignments/icmp-parameters/icmp-parameters.xhtml',46:'https://www.iana.org/assignments/icmpv6-parameters/icmpv6-parameters.xhtml',49:'https://www.ndss-symposium.org/wp-content/uploads/2019/02/ndss2019_03A-1_LePochat_paper.pdf',56:'https://www.caida.org/workshops/wide/0611/slides/manning-wide0611.pdf',61:'https://www.sidn.nl/downloads/4e5otgyyJap464iRzmZeN9/47f08b1511627967ff2280f014e0ff23/Fragmentation__truncation__and_timeouts_are_large_DNS_messages_falling_to_bits.pdf',68:'https://doi.org/10.1007/978-3-031-85960-1_5',70:'https://arxiv.org/pdf/2302.11393v1',71:'https://www.usenix.org/system/files/conference/woot14/woot14-ullrich.pdf',72:'https://indico.dns-oarc.net/event/24/contributions/378/attachments/353/613/2015-old-j-root.pdf'}
def timestamp():return dt.datetime.now(dt.timezone.utc).isoformat()
def request(url,audit,number,index):
 a=dict(url=url,started_at=timestamp())
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'JumpServe scholarly assessment (bounded direct-reference retrieval)'}),timeout=25,context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')) as r:
   raw=r.read(20_000_001)
   if len(raw)>20_000_000:raise ValueError('20MB per-resource limit')
   a.update(status=r.status,retrieved_url=r.url,headers=dict(r.headers),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
   path=ROOT/f'raw/literature/{number:02d}-{index}.bin';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);a['path']=str(path.relative_to(ROOT))
  a['ended_at']=timestamp();audit.append(a);return raw,a
 except Exception as e:a.update(status='failed',reason=str(e),ended_at=timestamp());audit.append(a);return None,a
def normalize(t):return re.sub('[^a-z0-9]','',t.lower())
def retrieve(source):
 n=source['reference_number'];dest=ROOT/f'raw/literature/{n:02d}-record.json'
 if dest.exists():return json.loads(dest.read_text())
 s=dict(source);audit=[];c=s['citation'];rfc=re.findall(r'RFC\s*(\d+)\.',c)
 if rfc:s['source_url']=f'https://www.rfc-editor.org/rfc/rfc{rfc[-1]}.txt'
 if n==54:s['doi']='10.1145/3230543.3230547';s['source_url']='https://doi.org/'+s['doi']
 if n in URLS:s['source_url']=URLS[n]
 candidates=[s['source_url']] if s['source_url'] else []
 title=re.search('“(.*?)”',c)
 title=title.group(1).replace('- ','') if title else c.split('. “')[-1].split('. In:')[0]
 if s['kind']=='research' and not s.get('doi'):
  query='https://api.crossref.org/works?rows=2&query.title='+urllib.parse.quote(title)
  raw,_=request(query,audit,n,0)
  if raw:
   try:
    for item in json.loads(raw)['message']['items']:
     found=(item.get('title') or [''])[0]
     if difflib.SequenceMatcher(None,normalize(title),normalize(found)).ratio()>=0.87:
      s['doi']=item['DOI'];s['metadata_match']=dict(title=found,doi=item['DOI'],method='Crossref exact-normalized or>=0.87title similarity; metadata discovery only')
      if not candidates:candidates.append('https://doi.org/'+item['DOI'])
      candidates.extend(x['URL'] for x in item.get('link',[]) if x.get('content-type')=='application/pdf');break
   except (ValueError,KeyError):pass
 if n==5:candidates=['https://buchshop.bod.de/ddos-stefan-behte-9783819226212']
 if n==25:candidates.append('https://insinuator.net/2015/11/some-notes-on-the-drop-ipv6-fragments-vs-this-will-break-dnssec-debate/')
 selected=None;retrieved=[]
 for url in dict.fromkeys(candidates):
  raw,a=request(url,audit,n,len(audit));
  if raw is None:continue
  retrieved.append(a)
  pdf=raw.startswith(b'%PDF');standard=s['kind']=='standard' and b'Request for Comments' in raw[:4000]
  original=s['kind']!='research' and n not in (5,21)
  if pdf or standard or original:selected=a;break
  text=raw.decode('utf8','replace')
  links=re.findall(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',text,re.I)
  links+=re.findall(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',text,re.I)
  for link in dict.fromkeys(links):
   if len(audit)>=10:break
   data,b=request(urllib.parse.urljoin(a['retrieved_url'],html.unescape(link)),audit,n,len(audit))
   if data and data.startswith(b'%PDF'):selected=b;break
  if selected:break
 s['retrieval_attempts']=audit
 s.update(access_status='full-text' if selected else 'landing-only' if retrieved else 'unavailable',review_status='retrieved-unreviewed' if selected else 'unavailable-full-text',retrieved_url=selected['retrieved_url'] if selected else None,retrieved_version='Original response bytes retrieved '+selected['ended_at']+'; '+selected.get('headers',{}).get('Last-Modified','no server version date') if selected else None,sha256=selected['sha256'] if selected else None,byte_count=selected['bytes'] if selected else None,raw_path=selected['path'] if selected else None,pages=None,findings=None,limitations='Retrieved original bytes have not been substantively reviewed. Figures/tables/appendices unexamined.' if selected else 'Full text unavailable through recorded attempts; metadata or landing pages do not establish findings.')
 dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n');return s
def main():
 seeds=json.loads((ROOT/'evidence/source-seeds-v1.json').read_text())
 with cf.ThreadPoolExecutor(max_workers=4) as pool:
  rows=[]
  for row in pool.map(retrieve,seeds):rows.append(row);print(row['reference_number'],row['access_status'],flush=True)
 (ROOT/'evidence/source-retrieval-v1.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps(dict(records=len(rows),full_text=sum(s['access_status']=='full-text' for s in rows))))
FOLLOWUP={35:['https://users.cs.northwestern.edu/~ychen/Papers/sigcomm13.pdf'],42:['https://www.potaroo.net/ispcol/2017-08/xtn-hdrs.pdf'],43:['https://www.potaroo.net/ispcol/2023-11/dns-ipv6.pdf'],44:['https://ispcol.potaroo.net/2024-02/truncation.html'],49:['https://arxiv.org/pdf/1806.01156v1'],9:['https://publica.fraunhofer.de/entities/publication/d4978aa4-f59c-4f6c-a6cd-7cae1402b5ca'],68:['https://pure.mpg.de/rest/items/item_3633648_5/component/file_3640592/content'],69:['https://pure.mpg.de/rest/items/item_3620691']}
FOLLOWUP.update({10:['https://www.caida.org/catalog/papers/2001_dnsmeasroot/dmr.pdf'],11:['https://www.caida.org/catalog/papers/2008_root_internet/root_internet.pdf'],42:['https://ispcol.potaroo.net/2017-08/xtn-hdrs.html'],43:['https://ispcol.potaroo.net/2023-11/dns-ipv6.html'],62:['https://ant.isi.edu/~johnh/PAPERS/Schmidt17a.pdf']})
def followup_one(s):
 s=dict(s);n=s['reference_number'];audit=list(s['retrieval_attempts']);candidates=list(FOLLOWUP.get(n,[]));selected=None
 if s['access_status']=='full-text':return s
 # Discover lawful author/repository copies of the same direct work, not its references.
 if s.get('doi'):
  data,a=request('https://api.openalex.org/works/https://doi.org/'+s['doi'],audit,n,len(audit))
  if data:
   try:
    work=json.loads(data)
    for loc in work.get('locations',[]):
     if loc.get('is_oa'):
      if loc.get('pdf_url'):candidates.append(loc['pdf_url'])
      elif loc.get('landing_page_url') and 'pure.mpg.de' in loc['landing_page_url']:candidates.append(loc['landing_page_url'])
   except ValueError:pass
 for url in list(dict.fromkeys(candidates))[:4]:
  data,a=request(url,audit,n,len(audit))
  if not data:continue
  if '/rest/items/' in url and not data.startswith(b'%PDF'):
   try:
    meta=json.loads(data)
    for f in meta.get('files',[]):
     if f.get('visibility')=='PUBLIC' and f.get('mimeType')=='application/pdf':
      b,z=request('https://pure.mpg.de'+f['content'],audit,n,len(audit))
      if b and b.startswith(b'%PDF'):selected=z;break
   except ValueError:pass
  elif data.startswith(b'%PDF') or n in (42,43,44):selected=a
  else:
   content=data.decode('utf8','replace');links=re.findall(r'(?:href|content)=["\']([^"\']+(?:\.pdf|/download)[^"\']*)["\']',content,re.I)
   for link in links[:2]:
    b,z=request(urllib.parse.urljoin(a['retrieved_url'],html.unescape(link)),audit,n,len(audit))
    if b and b.startswith(b'%PDF'):selected=z;break
  if selected:break
 if selected:s.update(access_status='full-text',review_status='retrieved-unreviewed',retrieved_url=selected['retrieved_url'],retrieved_version='Follow-up original response '+selected['ended_at']+'; '+selected['headers'].get('Last-Modified','no server version date'),sha256=selected['sha256'],byte_count=selected['bytes'],raw_path=selected['path'],limitations='Retrieved full bytes, no substantive review; all figures/tables/appendices unexamined.')
 s['retrieval_attempts']=audit;return s
def followup(known=False):
 rows=json.loads((ROOT/f'evidence/source-retrieval-v{2 if known else 1}.json').read_text())
 with cf.ThreadPoolExecutor(max_workers=4) as pool:
  out=[]
  chosen=lambda s:followup_one(s) if not known or s['reference_number'] in (10,11,42,43,62) else s
  for s in pool.map(chosen,rows):out.append(s);print(s['reference_number'],s['access_status'],flush=True)
 target=ROOT/f'evidence/source-retrieval-v{3 if known else 2}.json'
 if target.exists():raise RuntimeError('Preserve existing follow-up')
 target.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps(dict(full_text=sum(s['access_status']=='full-text' for s in out))))
def identity_correction():
 rows=json.loads((ROOT/'evidence/source-retrieval-v3.json').read_text());out=[]
 for s in rows:
  if s['reference_number'] in (18,49):
   n=s['reference_number'];s=dict(s);s['rejected_resources']=[dict(url=s['retrieved_url'],sha256=s['sha256'],raw_path=s['raw_path'],reason='First-page title/author identity differs from cited work; retained but rejected as citation evidence')]
   s.update(access_status='unavailable',review_status='unavailable-full-text',retrieved_url=None,sha256=None,raw_path=None,byte_count=None,pages=None)
   FOLLOWUP[n]=['https://arxiv.org/pdf/2309.03525v2'] if n==18 else ['https://tranco-list.eu/assets/tranco-ndss19.pdf']
   s=followup_one(s);s['limitations']+=' Previous wrong-identity PDF rejected and retained in retrieval history. First-page identity check is not substantive review.'
  out.append(s);print(s['reference_number'],s['access_status'],flush=True)
 target=ROOT/'evidence/source-retrieval-v4.json'
 if target.exists():raise RuntimeError('Preserve correction history')
 target.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
if __name__=='__main__':
 if sys.argv[1:]==['--identity-correction']:identity_correction()
 elif sys.argv[1:]==['--followup']:followup()
 elif sys.argv[1:]==['--known-fallback']:followup(True)
 elif not sys.argv[1:]:main()
 else:sys.exit('Unknown bounded retrieval mode')
