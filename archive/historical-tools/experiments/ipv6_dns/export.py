"""Deterministic public derived-data archive, excluding original copyrighted source bytes."""
import csv,hashlib,io,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent;OUT=ROOT.parents[2]/'jumpserve-front-end/public/module/ipv6-dns-study'
def main():
 d=json.loads((ROOT/'evidence/assessment-v2.json').read_text());OUT.mkdir(parents=True,exist_ok=True);members={}
 for key in ('protocols','campaigns','configurations','runs','summaries','published','comparisons','sources','claims','controls','validation','manifest','provenance','costs','label_definitions'):
  members[key+'.json']=(json.dumps(d[key],indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode()
 for key in ('measurements','published','comparisons'):
  buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=list(d[key][0]));writer.writeheader();writer.writerows(d[key]);members[key+'.csv']=buf.getvalue().encode()
 for p in sorted((ROOT/'protocols').glob('*.json')):members['protocols/'+p.name]=p.read_bytes()
 for p in sorted(ROOT.glob('*.py')):members['implementation/'+p.name]=p.read_bytes()
 members['README.md']=(ROOT/'README.md').read_bytes()
 members['archive-member-manifest.json']=(ROOT/'evidence/author-members-v1.json').read_bytes()
 members['published-context-and-figure-coverage.json']=(ROOT/'evidence/paper-context-v1.json').read_bytes()
 manifest={n:dict(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw)) for n,raw in sorted(members.items())};members['member-manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
 with zipfile.ZipFile(OUT/'data.zip','w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for n,raw in sorted(members.items()):info=zipfile.ZipInfo(n,(2026,10,5,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,raw,compresslevel=9)
 download={'data.zip':dict(sha256=hashlib.sha256((OUT/'data.zip').read_bytes()).hexdigest(),bytes=(OUT/'data.zip').stat().st_size)}
 (OUT/'download-manifest.json').write_text(json.dumps(download,indent=2)+'\n');print(json.dumps(download))
 (OUT/'paper-context.json').write_bytes((ROOT/'evidence/paper-context-v1.json').read_bytes())
if __name__=='__main__':main()
