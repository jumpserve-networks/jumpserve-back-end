"""Apply explicit human review notes and reconcile retrieved original-byte hashes."""
import datetime
import hashlib
import json
from pathlib import Path
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parent
URLS={3:'https://www.ietf.org/archive/id/draft-ietf-httpbis-http2-00.txt',17:'https://www.inf.ufpr.br/elias/papers/2018/SurveyNetNeutrality.pdf',43:'https://shenkaiwen.com/files/papers/DSN22_HDiff.pdf'}
def main():
    rows=json.loads((ROOT/'literature.json').read_text());notes=json.loads((ROOT/'review-notes.json').read_text())
    for row in rows[1:]:
        n=row['reference_number'];base=ROOT/'sources'/f'ref-{n:02}'
        path=next((base.with_suffix(ext) for ext in ('.pdf','.web','.txt') if base.with_suffix(ext).exists()),None)
        if path:
            row.update(download_status='downloaded',sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            if path.suffix=='.pdf':row['pages']=len(PdfReader(path).pages)
            if n in URLS:
                row.update(retrieved_url=URLS[n],retrieved_version='IETF Internet-Draft00 (2012), historical non-final standard' if n==3 else 'Author-hosted manuscript; title/authors verified from front matter')
                row.setdefault('retrieval_audit',[]).append(dict(url=URLS[n],status='downloaded and identity checked',sha256=row['sha256'],bytes=path.stat().st_size,at=datetime.datetime.now(datetime.timezone.utc).isoformat()))
            if path.suffix=='.web':row['retrieved_version']='Current public web/README original bytes, retrieved2026-10-04; not cited-date snapshot'
            if str(n) in notes:
                row.update(notes[str(n)]);row.setdefault('review_status','partial')
                if row['review_status']=='unreviewed':row['review_status']='partial'
                if path.suffix=='.pdf' and n not in (17,41,43):row['retrieved_version']='Publisher or author PDF; title and authors checked; exact publication revision not independently established'
            else:row.update(review_status='unreviewed',findings='Full text retrieved; no substantive source review completed.',limitations='Download does not imply review. No findings from this source used beyond citation identity.')
        else:
            row.update(review_status='unavailable',sha256=None,findings='Bibliographic metadata only; no full-text findings adopted.',limitations='Full text unavailable after documented public retrieval attempts. Supporting coverage remains incomplete.')
    (ROOT/'literature.json').write_text(json.dumps(rows,indent=2)+'\n')
    counts={state:sum(r['review_status']==state for r in rows) for state in ('complete','partial','unreviewed','unavailable')}
    print(json.dumps(dict(total=len(rows),reviews=counts,unavailable=[r['reference_number'] for r in rows if r['review_status']=='unavailable'])))
if __name__=='__main__':main()
