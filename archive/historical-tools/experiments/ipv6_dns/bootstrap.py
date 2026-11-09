"""Retrieve two fixed originals and safely extract only the code needed for reproduction."""
import hashlib,json,tarfile,urllib.request
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parent
INPUTS={
 'main.pdf':('https://pure.mpg.de/rest/items/item_3670144_1/component/file_3670145/content','f6378fb3de19aa8d3204f4d3a2028f95497f334862a5298aa6678ef1b816290b',3_000_000),
 'measurement_scripts_and_configs.tar.gz':('https://data.measurement.network/dns-mtu-msmt/raw/measurement_scripts_and_configs.tar.gz','72d505c5980b4be5c55c64a25eb10a147eebac1ec0c0f70aaf40cc49948ce9e0',110_000_000)}
def main():
 for name,(url,digest,limit) in INPUTS.items():
  p=ROOT/'raw'/name;p.parent.mkdir(exist_ok=True)
  if p.exists():raw=p.read_bytes()
  else:
   with urllib.request.urlopen(url,timeout=60) as response:raw=response.read(limit+1)
  if len(raw)>limit or hashlib.sha256(raw).hexdigest()!=digest:raise RuntimeError('Input identity changed: '+name)
  if not p.exists():p.write_bytes(raw)
 with tarfile.open(ROOT/'raw/measurement_scripts_and_configs.tar.gz') as archive:
  for item in archive.getmembers():
   path=PurePosixPath(item.name)
   if not item.isfile() or path.is_absolute() or '..' in path.parts:continue
   if not (item.name.endswith('/plot/plot_overview_absolute.py') or item.name.endswith('/scripts/aggregate_dns_data.py') or item.name.endswith('/scripts/create_stats.py')):continue
   p=ROOT/'author'/item.name;raw=archive.extractfile(item).read()
   if p.exists() and p.read_bytes()!=raw:raise RuntimeError('Do not overwrite modified original source')
   p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
 print(json.dumps({'originals':list(INPUTS),'byte_hashes_verified':True,'extraction':'Three original reproduction scripts only; no tar commands executed'}))
if __name__=='__main__':main()
