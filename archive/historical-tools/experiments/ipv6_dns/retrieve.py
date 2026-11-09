"""Retrieve only predeclared historical DNS summaries; keep original bytes and every attempt."""
import argparse, concurrent.futures as cf, datetime as dt, hashlib, json, ssl, time, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE='https://data.measurement.network/dns-mtu-msmt/'
def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()
def fetch(item):
 date,kind=item;rel=f'out_data/{date}/stats/all_psl_domains.json.gz' if kind=='dns' else f'pcap_agregates/{date}.json'
 target=ROOT/'raw/archive'/date/(kind+'.json'+('.gz' if kind=='dns' else ''))
 audit=target.with_suffix(target.suffix+'.attempts.json');target.parent.mkdir(parents=True,exist_ok=True)
 attempts=json.loads(audit.read_text()) if audit.exists() else []
 if target.exists():
  raw=target.read_bytes()
 else:
  raw=None
  # Preserve local resolution failures but do not count them as remote retrievals.
  remote_attempts=[a for a in attempts if 'nodename nor servname' not in a.get('reason','')]
  for _ in range(max(0,2-len(remote_attempts))):
   attempt=dict(url=BASE+rel,started_at=stamp());start=time.monotonic()
   try:
    request=urllib.request.Request(BASE+rel,headers={'User-Agent':'JumpServe-research/1.0'})
    with urllib.request.urlopen(request,timeout=40,context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')) as response:
     data=response.read(20_000_001)
     if len(data)>20_000_000:raise ValueError('Artifact exceeds frozen20MB limit')
     attempt.update(status=response.status,retrieved_url=response.url,headers=dict(response.headers),bytes=len(data))
     raw=data;target.write_bytes(raw)
   except Exception as exc:attempt.update(status='failed',reason=str(exc))
   attempt.update(ended_at=stamp(),wall_seconds=time.monotonic()-start);attempts.append(attempt);audit.write_text(json.dumps(attempts,indent=2)+'\n')
   if raw is not None:break
 return dict(id=f'{date}-{kind}',date=date,kind=kind,url=BASE+rel,path=str(target.relative_to(ROOT)) if raw is not None else None,status='retrieved' if raw is not None else 'failed',reason=None if raw is not None else 'Historical daily artifact unavailable after declared attempts',bytes=len(raw) if raw is not None else None,sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,retrieval_attempts=attempts)
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-version',type=int,default=1,choices=range(1,100));args=parser.parse_args()
 destination=ROOT/f'evidence/archive-retrieval-v{args.output_version}.json'
 if destination.exists():raise RuntimeError('Preserve original retrieval results; choose a new output version')
 lock=json.loads((ROOT/'protocols/ipv6-dns-archive-v1.lock.json').read_text());protocol=ROOT/'protocols/ipv6-dns-archive-v1.json'
 if hashlib.sha256(protocol.read_bytes()).hexdigest()!=lock['sha256']:raise RuntimeError('Protocol changed')
 dates=[];d=dt.date(2025,4,10)
 while d<=dt.date(2025,9,14):dates.append(str(d));d+=dt.timedelta(days=1)
 start=time.monotonic();rows=[]
 with cf.ThreadPoolExecutor(max_workers=4) as executor:
  for row in executor.map(fetch,[(d,k) for d in dates for k in ('dns','pcap')]):
   rows.append(row)
   if len(rows)%20==0:print(json.dumps(dict(visited=len(rows),failed=sum(r['status']=='failed' for r in rows))),flush=True)
   if sum(r['bytes'] or 0 for r in rows)>150_000_000:raise RuntimeError('Frozen150MB download limit exceeded')
 out=dict(protocol_id=lock['id'],protocol_sha256=lock['sha256'],finished_at=stamp(),wall_seconds=time.monotonic()-start,records=rows)
 destination.parent.mkdir(exist_ok=True)
 destination.write_text(json.dumps(out,indent=2)+'\n')
 print(json.dumps(dict(planned=len(rows),retrieved=sum(r['status']=='retrieved' for r in rows),bytes=sum(r['bytes'] or 0 for r in rows),wall_seconds=out['wall_seconds'])))
if __name__=='__main__':main()
