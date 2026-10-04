"""Verify every adopted full-text hash against retained original bytes, not extracts."""
from collections import Counter
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
rows=json.loads((ROOT/'literature.json').read_text());checks=[]
for row in rows:
    n=row['reference_number'];expected=row.get('sha256')
    if not expected:
        assert row['review_status']=='unavailable'
        checks.append({'reference_number':n,'status':'unavailable','sha256':None});continue
    candidates=[ROOT/'sources/main-paper.pdf'] if n==0 else [ROOT/'sources'/f'ref-{n:02}.{suffix}' for suffix in ('pdf','web','txt')]
    # The finalizer adopts the first retained format in this order. A plain-text
    # web response and its text extraction can have identical bytes; this is not
    # an ambiguous hash or a reason to reject the retained original.
    original=next((p for p in candidates if p.is_file()),None)
    assert original is not None and hashlib.sha256(original.read_bytes()).hexdigest()==expected,(n,'Retained original differs from adopted hash')
    checks.append({'reference_number':n,'status':'hash verified','sha256':expected,'original_path':str(original.relative_to(ROOT))})
report={'status':'pass','inventory_sha256':hashlib.sha256((ROOT/'literature.json').read_bytes()).hexdigest(),'hash_verified':sum(c['status']=='hash verified' for c in checks),'unavailable':sum(c['status']=='unavailable' for c in checks),'reviews':dict(Counter(r['review_status'] for r in rows)),'checks':checks,'limitation':'Hash checks do not turn partial/unreviewed sources into complete reviews or prove document identity.'}
(ROOT/'evidence/source-hash-validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='checks'},indent=2))
