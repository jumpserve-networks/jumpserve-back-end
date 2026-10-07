"""Bounded operator import of externally executed, documented campaigns.

Validates record schemas and original file identities. It does not independently
establish that the supplied implementation executed or generated published data.
"""
from pathlib import Path
import json
import time
import uuid
from workflow import FIELDS, canonical, digest, identifier, text, validate_record

ALLOWED=set(FIELDS)-{'artifacts','publications','reviews'}
def prepare(path, import_id):
    path=Path(path).resolve();identifier(import_id)
    if path.stat().st_size>10_000_000:raise ValueError('External import metadata exceeds 10 MB; split versioned campaigns, never truncate.')
    raw=path.read_bytes();data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={'version','records','artifacts','provenance'} or data['version']!=1:raise ValueError('External import requires version 1, records, artifacts and provenance.')
    for field in ('producer','execution_revision','execution_dates','limitations'):text(data['provenance'].get(field),'provenance.'+field)
    if not isinstance(data['records'],list) or not 1<=len(data['records'])<=10000:raise ValueError('Declare 1–10000 complete external records.')
    if not isinstance(data['artifacts'],list) or not 1<=len(data['artifacts'])<=10:raise ValueError('Declare 1–10 original external files.')
    originals=[];total=len(raw);seen=set()
    for artifact in data['artifacts']:
        if not isinstance(artifact,dict) or set(artifact)!={'path','sha256','media_type','version','transformation'}:raise ValueError('Artifact needs path, sha256, media_type, version and transformation.')
        relative=Path(artifact['path']);file=(path.parent/relative).resolve()
        if relative.is_absolute() or not file.is_relative_to(path.parent) or file==path:raise ValueError('Artifact paths must remain inside the bundle directory; no external private file reads.')
        for field in ('media_type','version','transformation'):text(artifact[field],field)
        if file.stat().st_size>10_000_000:raise ValueError('An original file exceeds the 10 MB import limit.')
        original=file.read_bytes();total+=len(original)
        if total>30_000_000:raise ValueError('External import exceeds the 30 MB aggregate byte budget.')
        if digest(original)!=artifact['sha256'] or artifact['path'] in seen:raise ValueError('Original file hash differs or artifact path is duplicated.')
        seen.add(artifact['path']);originals.append(dict(artifact,raw=original))
    hashes={artifact['sha256'] for artifact in originals}
    records=[];identities=set()
    for entry in data['records']:
        if not isinstance(entry,dict) or set(entry)!={'kind','record'} or entry['kind'] not in ALLOWED:raise ValueError('External imports contain scientific records only; reviews/publications are separate.')
        kind=entry['kind'];row=validate_record(kind,entry['record'])
        if (kind,row['id']) in identities:raise ValueError('Duplicate external record identity.')
        identities.add((kind,row['id']))
        if kind=='runs':
            if row['input_sha256'] not in hashes or (row['output_sha256'] is not None and row['output_sha256'] not in hashes):raise ValueError('Every external run input and available output hash must identify an original supplied file.')
            row['provenance']={**row['provenance'],'external_import_sha256':digest(raw),'external_execution':data['provenance'],'import_validation':'Schema and original-byte hash checks only; execution and scientific interpretation need separate review.'}
        records.append(dict(kind=kind,record=row))
    records.sort(key=lambda entry:list(FIELDS).index(entry['kind']))
    return dict(import_id=import_id,original_sha256=digest(raw),original_bytes=len(raw),aggregate_bytes=total,records=records,artifacts=[{key:value for key,value in artifact.items() if key!='raw'} for artifact in originals],provenance=data['provenance'],scientific_validation='Not established by import'),raw,originals

def persist(prepared,raw,originals,study,actor):
    import store
    if not store.owns(study,actor):raise ValueError('Study ownership mismatch.')
    tick=time.monotonic();artifacts=[]
    for name,original,media,provenance in [('bundle',raw,'application/json',{'transformation':'none; original external metadata'})]+[(item['path'],item['raw'],item['media_type'],{key:value for key,value in item.items() if key!='raw'}) for item in originals]:
        if time.monotonic()-tick>180:raise ValueError('Import storage wall budget reached; already uploaded immutable bytes remain preserved. Retry the original import ID.')
        artifacts.append(dict(kind='artifacts',record=store.artifact(study,actor,original,str(uuid.uuid5(uuid.UUID(prepared['import_id']),name)),media,provenance)))
    store.append_bundle(study,actor,artifacts+prepared['records'])
