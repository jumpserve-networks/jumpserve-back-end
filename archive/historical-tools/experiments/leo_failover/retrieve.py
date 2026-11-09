"""Archive cited sources and record failures without claiming unread papers read."""
import concurrent.futures
import hashlib
import json
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parent
RESEARCH={11,12,15,16,17,20,24,25,26,27,30,35,41,44,45,48,50,51,53,54,55,57,65,66,67,68,69,70,71,72,75,77,78,87,90,91,92,95,96}
URLS={
12:'https://www.usenix.org/system/files/atc25-basak.pdf',
15:'https://vaibhavb007.github.io/papers/astrolabe.pdf',
16:'https://par.nsf.gov/servlets/purl/10438180',
17:'https://papers.ssrn.com/sol3/Delivery.cfm/5337566.pdf?abstractid=5337566',
24:'https://bpb-us-e1.wpmucdn.com/sites.mit.edu/dist/e/1448/files/2025/02/delportillo19a.pdf',
25:'https://tore.tuhh.de/bitstreams/965ce507-394e-4ff8-8ee9-a47e4d38986d/download',
26:'https://vtechworks.lib.vt.edu/server/api/core/bitstreams/906ec958-28db-4fb8-beae-1ad1809356a0/content',
27:'https://www.cs.colgate.edu/~jsommers/pubs/intertubes.pdf',
30:'https://dl.acm.org/doi/pdf/10.1145/3750832.3750835',
35:'https://diva-portal.org/smash/get/diva2%3A2013617/FULLTEXT01.pdf',
41:'https://cs.stanford.edu/~gakiwate/papers/sigmetrics24-satellite.pdf',
44:'https://arxiv.org/pdf/2406.11366',
45:'https://bdebopam.github.io/papers/imc2020-hypatia.pdf',
48:'https://wireless.ee.ucla.edu/pdf/pub/satellite-selection.pdf',
50:'https://www.usenix.org/system/files/nsdi23-lai-zeqi.pdf',
51:'https://tma.ifip.org/2024/wp-content/uploads/sites/13/2024/05/tma2024-final53.pdf',
53:'https://dl.acm.org/doi/pdf/10.1145/3748749.3749086',
54:'https://dl.acm.org/doi/pdf/10.1145/3636534.3649362',
55:'https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9968247',
57:'https://www.usenix.org/system/files/nsdi24-liu-lixin.pdf',
65:'https://iris.polito.it/retrieve/handle/11583/2972615/609130',
66:'https://spearlab.nl/papers/2024/starlinkWWW2024.pdf',
68:'https://bpb-us-e1.wpmucdn.com/sites.mit.edu/dist/e/1448/files/2025/02/pachler24c.pdf',
69:'https://bpb-us-e1.wpmucdn.com/sites.mit.edu/dist/e/1448/files/2025/02/pachler21a.pdf',
70:'https://systemarchitect.mit.edu/wp-content/uploads/2025/02/pachler24a.pdf',
71:'https://bpb-us-e1.wpmucdn.com/sites.mit.edu/dist/e/1448/files/2025/02/pachler20a.pdf',
72:'https://satmagazine.com/wp-content/uploads/2026/02/SM_Nov2021-1.pdf',
77:'https://icaiit.org/proceedings/11th_ICAIIT_1/1_9%20ICAIIT_2023_paper_4290.pdf',
87:'https://arxiv.org/pdf/2307.00402',
78:'https://conferences.sigcomm.org/imc/2014/papers/p1.pdf',
90:'https://ix.cs.uoregon.edu/~ram/papers/SIGCOMM-2025.pdf',
91:'https://wangshangguang.github.io/assets/TiansuanFinal1203.pdf',
92:'https://mint.nju.edu.cn/_upload/tpl/08/ac/2220/template2220/pdf/mwc16wr.pdf',
95:'https://arxiv.org/pdf/2205.10721',
96:'https://dl.acm.org/doi/pdf/10.1145/3748749.3749089'
}

def download(url):
    request=urllib.request.Request(url,headers={'User-Agent':'JumpServe academic reproduction/1.0'})
    with urllib.request.urlopen(request,timeout=25,context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')) as f:
        data=f.read(60*1024*1024)
        return data,f.geturl()

def retrieve(row):
    number=row['reference_number']
    if row['kind']!='research':
        row.update(download_status='web citation',reading_status='not a research paper',source_url=next(iter(row.pop('urls',[])),None))
        return row
    existing=ROOT/f'sources/ref-{number:02}.pdf'
    candidates=[]
    if number in URLS: candidates.append(URLS[number])
    candidates+=row.pop('urls',[])
    if row['kind']=='research' and not candidates:
        # Crossref is metadata only; retain exact candidate title for review.
        words=row['citation'].split('. ')
        title='. '.join(words[1:3])
        try:
            data,_=download('https://api.crossref.org/works?rows=1&query.bibliographic='+urllib.parse.quote(row['citation']))
            item=json.loads(data)['message']['items'][0]
            row['metadata_title']=item.get('title',[''])[0]
            row['doi_candidate']=item.get('DOI')
            # A fuzzy bibliographic match is metadata, not verified identity.
            # Add a confirmed author/publisher URL to URLS before downloading.
            row['metadata_pdf_candidates']=[link['URL'] for link in item.get('link',[]) if link.get('content-type')=='application/pdf']
        except Exception as exc: row['metadata_error']=type(exc).__name__
    row.setdefault('attempts',[])
    if existing.exists():
        candidates=[]
    for url in dict.fromkeys(candidates):
        try:
            data,final=download(url)
            if b'%PDF-' in data[:100]:
                existing.write_bytes(data)
                row['source_url']=final
                row['download_status']='downloaded'
                break
            row['attempts'].append({'url':url,'outcome':'HTML/other content; not a PDF'})
        except Exception as exc:
            row['attempts'].append({'url':url,'outcome':str(exc)[:180]})
    if existing.exists():
        try:
            reader=PdfReader(existing)
            text='\n'.join(f'\nPAGE {i+1}\n'+page.extract_text() for i,page in enumerate(reader.pages))
            existing.with_suffix('.txt').write_text(text)
            row.update(download_status='downloaded',sha256=hashlib.sha256(existing.read_bytes()).hexdigest(),pages=len(reader.pages),reading_status=row.get('reading_status','pending'),text_characters=len(text))
        except Exception as exc:
            row.update(download_status='invalid PDF',reading_status='unread',error=type(exc).__name__)
    else:
        # An explicitly recorded full author-text review is independent of PDF
        # availability. Abstracts and metadata do not satisfy this condition.
        reviewed_web=row.get('web_text_reviewed') is True and row.get('reading_status')=='reviewed'
        row.update(download_status='unavailable' if row['kind']=='research' else 'web citation',reading_status='reviewed' if reviewed_web else 'unread' if row['kind']=='research' else 'not a research paper')
        row.setdefault('source_url',next(iter(candidates),None))
    return row

def main():
    text=(ROOT/'sources/page-13.txt').read_text().split('References\n',1)[1]+'\n'+(ROOT/'sources/page-14.txt').read_text()+'\n'+(ROOT/'sources/page-15.txt').read_text()
    text=re.sub(r'IMC .+?Vaibhav Bhosale et al\.', '',text)
    text=re.sub(r'Assessing LEO Satellite Networks.+?Madison, WI, USA','',text)
    refs=[]
    for match in re.finditer(r'\[(\d+)\]\s+(.*?)(?=\[\d+\]\s|\Z)',text,re.S):
        n=int(match[1]); citation=re.sub(r'\s+',' ',match[2]).strip()
        citation=re.sub(r'(\w)- (\w)',r'\1\2',citation)
        urls=re.findall(r'https?://[^\s]+',citation)
        refs.append({'reference_number':n,'citation':citation,'kind':'research' if n in RESEARCH else 'web/data/regulation','urls':urls})
    assert [r['reference_number'] for r in refs]==list(range(1,97))
    old={r['reference_number']:r for r in json.loads((ROOT/'literature.json').read_text())} if (ROOT/'literature.json').exists() else {}
    refs=[dict(r,**{k:v for k,v in old.get(r['reference_number'],{}).items() if k in ['reading_status','reading_notes','source_url','version_note','attempts']}) for r in refs]
    notes=json.loads((ROOT/'review-notes.json').read_text()) if (ROOT/'review-notes.json').exists() else {}
    refs=[dict(r,**notes.get(str(r['reference_number']),{})) for r in refs]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(retrieve,refs))
    (ROOT/'literature.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps({'references':len(rows),'research':sum(r['kind']=='research' for r in rows),'downloaded':sum(r['download_status']=='downloaded' for r in rows),'unavailable':[r['reference_number'] for r in rows if r['download_status']=='unavailable']},indent=2))

if __name__=='__main__': main()
