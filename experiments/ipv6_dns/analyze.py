"""Independent, identity-matched historical summary reanalysis. No network probes."""
import ast,contextlib,datetime as dt,gzip,hashlib,io,json,math,platform,re,statistics,time,types
from collections import defaultdict
from pathlib import Path
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parent
AUTHOR=ROOT/'author/srv/script_backup/crontrol-hosts/backend.dns-mtu.measurement.network/opt/msmt/msmt'
METRICS=['TIMEOUT','SERVFAIL','edns0_fallback','tcp_fallback','udp_fragments','udp_packet_too_big']
MTUS=['mtu1500','mtu1280-pmtud','mtu1280-nopmtud','mtu1280onpath-nopmtud']
EPOCHS={'tcp-error':('2025-04-10','2025-05-23'),'pre-lgi':('2025-05-25','2025-06-14'),'current':('2025-06-25','2025-09-14')}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def stamp():return dt.datetime.now(dt.timezone.utc).isoformat()
def config(case,cohort):
 match=re.fullmatch(r'(mtu1500|mtu1280-pmtud|mtu1280-nopmtud|mtu1280onpath-nopmtud)(-lgi)?-(v4-only|v6-only|ds)-edns(512|1232|4096)',case)
 if not match:raise ValueError('Unknown configuration '+case)
 return dict(id=cohort+'--'+case,case_id=case,cohort=cohort,mtu=match[1],upstream='lgi' if match[2] else 'no-lgi',family=match[3],edns_bytes=int(match[4]),details={'historical':'2025 measurement configuration; no new test resources requested','cohort_definition':'Author create_stats classifies DNSSEC by any observed signature across configurations; packet capture cohort label comes from its query campaign. Their population identities are not interchangeable.'},requested_resources={'new_network_hosts':0},actual_resources={'execution':'local archived-data reanalysis','platform':platform.platform()})
def expected_configs():
 return [config(f'{mtu}{"-lgi" if upstream=="lgi" else ""}-{family}-edns{edns}',cohort) for cohort in ['dnssec','no-dnssec'] for mtu in MTUS for upstream in ['lgi','no-lgi'] for family in ['v4-only','v6-only','ds'] for edns in [512,1232,4096]]
def num(x):return isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) and x>=0 and int(x)==x
def metric(n,d,unit,reason=None):
 if not num(n) or not num(d):return dict(numerator=n,denominator=d,value=None,status='missing' if n is None or d is None else 'invalid',reason=reason or 'Missing or invalid recorded count',units=unit)
 if d==0:return dict(numerator=n,denominator=d,value=None,status='invalid',reason='Zero denominator; percentage undefined',units=unit)
 if n>d and unit!='TCP/UDP ratio percent':return dict(numerator=n,denominator=d,value=None,status='invalid',reason='Numerator exceeds denominator',units=unit)
 return dict(numerator=n,denominator=d,value=100*n/d,status='recorded',reason=reason,units=unit)
def original_fold(dates):
 """Execute original function bytes via AST; adapter restricts file reads to chosen archive days."""
 source=AUTHOR/'plot/plot_overview_absolute.py';tree=ast.parse(source.read_text())
 function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='read_files')
 dnsfiles=[str(ROOT/f'raw/archive/{d}/dns.json.gz') for d in dates]
 def original_path(p):
  if p.startswith('/srv/data/pcap_agregates/'):
   d=Path(p).stem
   if d not in dates:raise ValueError('Author read outside declared epoch')
   return str(ROOT/f'raw/archive/{d}/pcap.json')
  return p
 env=dict(json=json,gzip=gzip,glob=types.SimpleNamespace(glob=lambda _:dnsfiles),dateutil=types.SimpleNamespace(parser=types.SimpleNamespace(isoparse=dt.datetime.fromisoformat)),os=types.SimpleNamespace(path=types.SimpleNamespace(isfile=lambda p:Path(original_path(p)).is_file())),open=lambda p,*a,**kw:open(original_path(p),*a,**kw))
 # Preserve expected date basename while opening exact unchanged downloaded bytes.
 virtual=[f'/srv/analysis/{d}.json.gz' for d in dates]
 env['glob']=types.SimpleNamespace(glob=lambda _:virtual)
 env['gzip']=types.SimpleNamespace(open=lambda p,*a,**kw:gzip.open(ROOT/f'raw/archive/{Path(p).name.split(".")[0]}/dns.json.gz',*a,**kw))
 exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),env)
 with contextlib.redirect_stdout(io.StringIO()):result=env['read_files']('fixed-archive-inputs')
 return result
def published():
 paper=PdfReader(ROOT/'raw/main.pdf');rows=[]
 for page,figure,epoch in [(17,13,'pre-lgi'),(18,14,'current'),(19,15,'tcp-error')]:
  blocks=[];current=[]
  for line in paper.pages[page-1].extract_text().splitlines():
   if re.fullmatch(r'(?:-?\d+\.\d{2}\s*){12}',line.strip()):current.append([float(v) for v in line.split()])
   elif current:blocks.append(current);current=[]
  if current:blocks.append(current)
  if len(blocks)!=4 or any(len(b)!=16 for b in blocks):raise ValueError('Published figure topology differs')
  # Extraction order is bottom-to-top within each group, confirmed against rendered labels.
  for fidx,family in enumerate(['v4-only','v6-only']):
   for ri,values in enumerate(blocks[fidx]):
    upstream,cohort=[('lgi','no-dnssec'),('lgi','dnssec'),('no-lgi','no-dnssec'),('no-lgi','dnssec')][ri//4];mtu=MTUS[ri%4]
    for ci,v in enumerate(values):
     edns=[1232,4096][ci//6];m=METRICS[ci%6];case=f'{mtu}{"-lgi" if upstream=="lgi" else ""}-{family}-edns{edns}';id=cohort+'--'+case
     rows.append(dict(id=f'fig{figure}{"ab"[fidx]}-{ri}-{ci}',source_number=0,location=f'PDF page{page}, Figure{figure}{"ab"[fidx]}, {cohort}, {upstream}, {mtu}, EDNS{edns}, {m}',configuration_id=id,epoch=epoch,metric=m,value=v,units='percent',extraction='pypdf numerical cell text with rendered-axis mapping; no plot digitization',paper_sha256=sha(ROOT/'raw/main.pdf')))
 return rows
def main():
 started=stamp();start=time.monotonic();protocol=ROOT/'protocols/ipv6-dns-archive-v1.json';lock=json.loads((ROOT/'protocols/ipv6-dns-archive-v1.lock.json').read_text());assert sha(protocol)==lock['sha256']
 manifest=json.loads((ROOT/'evidence/archive-retrieval-v2.json').read_text())['records']
 configurations=expected_configs();runs=[];measurements=[];problems=[];index={r['id']:r for r in manifest}
 for artifact in manifest:
  if artifact['path'] and sha(ROOT/artifact['path'])!=artifact['sha256']:raise ValueError('Downloaded bytes changed')
 for date in sorted({r['date'] for r in manifest}):
  inputs={k:index[f'{date}-{k}'] for k in ['dns','pcap']};parsed={};reason=[]
  for k,a in inputs.items():
   if a['status']=='retrieved':
    try:parsed[k]=json.loads(gzip.decompress((ROOT/a['path']).read_bytes()) if k=='dns' else (ROOT/a['path']).read_bytes())
    except Exception as error:reason.append(f'{k}invalid JSON: {error}')
   else:reason.append(f'{k}missing historical input')
  epoch=next((k for k,(lo,hi) in EPOCHS.items() if lo<=date<=hi),None)
  status='failed' if reason else 'complete' if epoch else 'excluded'
  runs.append(dict(id=date,protocol_id=lock['id'],stage='main',epoch=epoch,status=status,reason='; '.join(reason) if reason else None if epoch else 'Outside predeclared published fold windows',original_execution_date=date,started_at=started,ended_at=None,analysis_version='ipv6-dns-assessment-v1',analysis_sha256=sha(Path(__file__)),raw_sha256={k:a['sha256'] for k,a in inputs.items()},source_urls={k:a['url'] for k,a in inputs.items()},requested_resources={'new_network_hosts':0},actual_resources={'input_bytes':sum(a['bytes'] or 0 for a in inputs.values())}))
  for c in configurations:
   case=c['case_id'];cohort=c['cohort'];s=parsed.get('dns',{}).get(cohort,{}).get(case);p=parsed.get('pcap',{}).get(cohort.replace('-','_'),{}).get(case,{}).get({'v4-only':'v4','v6-only':'v6','ds':'both'}[c['family']]);counts={};metrics={}
   if s:
    count=s.get('count');statuses=s.get('status',{})
    if not num(count) or not all(num(v) for v in statuses.values()) or sum(statuses.values())!=count:problems.append(dict(date=date,configuration_id=c['id'],check='Status conservation',payload=s))
    counts['ns_sets']=count;counts['statuses']=statuses
   for m in METRICS:
    if m in ('TIMEOUT','SERVFAIL'):
     n=s.get('status',{}).get(m) if s else None;d=s.get('count') if s else None;unit='NS-set outcome percent'
    else:
     field={'edns0_fallback':'client_edns_fallback','tcp_fallback':'count','udp_fragments':'frag','udp_packet_too_big':'packet_too_big'}[m]
     n=p.get('TCP' if m=='tcp_fallback' else 'UDP',{}).get(field) if p else None;d=p.get('UDP',{}).get('count') if p else None;unit='TCP/UDP ratio percent' if m=='tcp_fallback' else 'observed UDP server percent'
    z=metric(n,d,unit);metrics[m]=z
    measurements.append(dict(run_id=date,configuration_id=c['id'],metric=m,**z))
   counts.update(udp_servers=p.get('UDP',{}).get('count') if p else None,tcp_servers=p.get('TCP',{}).get('count') if p else None)
   # DNS cohort partition checks share upstream author aggregation assumptions.
   if s and cohort=='dnssec':
    other=parsed['dns'].get('no-dnssec',{}).get(case);allc=parsed['dns'].get('all',{}).get(case)
    if other and allc and s['count']+other['count']!=allc['count']:problems.append(dict(date=date,configuration_id=c['id'],check='DNS cohort partition'))
 ended=stamp()
 for run in runs:run['ended_at']=ended
 grouped=defaultdict(list)
 for m in measurements:grouped[(m['configuration_id'],m['metric'])].append(m)
 summaries=[]
 for epoch,(lo,hi) in EPOCHS.items():
  for c in configurations:
   stats={}
   for metric_name in METRICS:
    allrows=[r for r in grouped[(c['id'],metric_name)] if lo<=r['run_id']<=hi];valid=[r for r in allrows if r['status']=='recorded'];values=[r['value'] for r in valid];filtered=[r for r in valid if not '2025-05-20'<=r['run_id']<='2025-05-24']
    family=c['family'];other=c['id'].replace('-v6-only-','-v4-only-')
    paired={r['run_id']:r for r in grouped.get((other,metric_name),[]) if r['status']=='recorded'} if family=='v6-only' else {}
    differences=[r['value']-paired[r['run_id']]['value'] for r in valid if r['run_id'] in paired]
    stats[metric_name]=dict(mean=statistics.fmean(values) if values else None,min=min(values) if values else None,max=max(values) if values else None,n=len(values),planned=len(allrows),missing=sum(r['status']=='missing' for r in allrows),invalid=sum(r['status']=='invalid' for r in allrows),pooled=100*sum(r['numerator'] for r in valid)/sum(r['denominator'] for r in valid) if valid else None,routing_sensitivity_mean=statistics.fmean(r['value'] for r in filtered) if filtered else None,routing_sensitivity_n=len(filtered),paired_v6_minus_v4_mean_pp=statistics.fmean(differences) if differences else None,paired_days=len(differences),units=allrows[0]['units'],interval_kind='descriptive daily min/max; no inferential confidence interval')
   summaries.append(dict(id=epoch+'--'+c['id'],configuration_id=c['id'],epoch=epoch,statistics=stats,interval_kind='descriptive'))
 bysummary={(s['configuration_id'],s['epoch']):s for s in summaries};published_rows=published();comparisons=[]
 for p in published_rows:
  s=bysummary[(p['configuration_id'],p['epoch'])]['statistics'][p['metric']];value=s['mean'];delta=value-p['value'] if value is not None else None
  comparisons.append(dict(id=p['id'],published_id=p['id'],configuration_id=p['configuration_id'],epoch=p['epoch'],metric=p['metric'],reproduced=value,published=p['value'],difference_pp=delta,assessment='inconclusive' if delta is None else 'reproduced' if abs(delta)<=0.0051 else 'discrepant',valid_days=s['n'],planned_days=s['planned']))
 author_checks=[]
 for epoch,(lo,hi) in EPOCHS.items():
  dates=[r['id'] for r in runs if lo<=r['id']<=hi and r['status']=='complete'];author=original_fold(dates)
  for c in configurations:
   if c['family']=='ds' or c['edns_bytes']==512:continue
   for m in METRICS:
    original=author[c['family'][:2]][c['upstream']][c['cohort']][c['mtu']][str(c['edns_bytes'])][m]
    ours=bysummary[(c['id'],epoch)]['statistics'][m]['mean'];equal=ours is not None and abs(original-ours)<1e-10
    author_checks.append(dict(epoch=epoch,configuration_id=c['id'],metric=m,original_fold=original,independent_fold=ours,agrees=equal))
 raw_manifest=[]
 for p in sorted((ROOT/'raw').rglob('*')):
  if p.is_file() and 'pages' not in p.parts:raw_manifest.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p),bytes=p.stat().st_size))
 out=dict(analysis_version='ipv6-dns-assessment-v1',started_at=started,ended_at=ended,wall_seconds=time.monotonic()-start,configuration_count=len(configurations),configurations=configurations,runs=runs,measurements=measurements,summaries=summaries,published=published_rows,comparisons=comparisons,validation=dict(independent_checks=problems,original_fold_comparisons=len(author_checks),original_fold_disagreements=sum(not x['agrees'] for x in author_checks),author_checks=author_checks,status_counts={s:sum(r['status']==s for r in runs) for s in ['complete','failed','excluded']},published_cells=len(comparisons),reproduced_cells=sum(r['assessment']=='reproduced' for r in comparisons),discrepant_cells=sum(r['assessment']=='discrepant' for r in comparisons)),manifest=raw_manifest)
 target=ROOT/'evidence/reanalysis-v1.json'
 if target.exists():raise RuntimeError('Preserve existing results; use a new analysis version')
 target.write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
 print(json.dumps({k:v for k,v in out['validation'].items() if k!='author_checks'},indent=2))
if __name__=='__main__':main()
