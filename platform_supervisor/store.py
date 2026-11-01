"""Fixed-project Supabase transport; no keys or private paths enter agent input."""
import json
import os
import re
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .common import canonical, digest, identity, save, Hold, network_opener

ORIGIN = 'https://regphejnlvfpyokpniny.supabase.co'
BUCKET = 'platform-supervisor-private'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise Hold('Control-plane redirects are disabled.')

class Store:
    def __init__(self, key, spool, worker):
        if not callable(key) and (not key or len(key)>10000): raise Hold('Platform persistence credential is unavailable.')
        self.key, self.spool, self.worker = key, Path(spool), str(uuid.UUID(worker))
        self.task = None
        self.opener = network_opener(NoRedirect())

    def request(self, path, method='GET', body=None, raw=False):
        research_read=method=='GET' and raw and re.fullmatch(r'/storage/v1/object/research-workflow-raw/[a-f0-9-]{36}/[a-f0-9]{64}',path)
        if (not research_read and not path.startswith(('/rest/v1/', '/storage/v1/object/'+BUCKET+'/'))) or '..' in path: raise ValueError('Unexpected platform persistence path.')
        data = body if isinstance(body, bytes) else canonical(body) if body is not None else None
        key=self.key() if callable(self.key) else self.key
        headers = {'apikey':key,'Authorization':'Bearer '+key,'Content-Type':'application/octet-stream' if isinstance(body,bytes) else 'application/json'}
        try:
            with self.opener.open(urllib.request.Request(ORIGIN+path,data=data,method=method,headers=headers),timeout=30) as response:
                original=response.read(5000001)
                if len(original)>5000000: raise Hold('Persistence response exceeds its bound.')
                return original if raw else json.loads(original) if original else None
        except urllib.error.HTTPError as error:
            raise Hold('Platform persistence request failed (HTTP '+str(error.code)+'); original action identities are retained.','reconciling') from None

    def rpc(self, operation, payload=None):
        return self.request('/rest/v1/rpc/platform_worker','POST',dict(p_operation=operation,p_worker=self.worker,p_task=self.task['id'] if self.task else None,p_token=self.task['lease_token'] if self.task else None,p_payload=payload or {}))

    def rows(self, table, **filters):
        if table not in ('runs','run_reconciliations','operations','changes','releases','artifacts'): raise ValueError('Unexpected platform relation.')
        if not self.task: raise ValueError('Task lease required.')
        return self.request('/rest/v1/platform_'+table+'?'+urllib.parse.urlencode(dict(select='*',task_id='eq.'+self.task['id'],limit='1000',**filters)))

    def artifact(self, name, original, provenance=None):
        if len(original)>5000000: raise Hold('Artifact exceeds the private 5 MB record limit.')
        sha=digest(original); task=self.task['id']; path=task+'/'+sha
        previous=self.rows('artifacts',id='eq.'+identity(task,'artifact:'+sha))
        if previous:
            if previous[0]['sha256']!=sha or previous[0]['byte_count']!=len(original):raise Hold('Retained artifact identity differs.')
            return previous[0]
        # Content-addressed upload can be reconciled after an uncertain response.
        save(self.spool/task/'artifacts'/sha,original)
        try: self.request('/storage/v1/object/'+BUCKET+'/'+path,'POST',original)
        except Hold:
            if self.request('/storage/v1/object/'+BUCKET+'/'+path,raw=True)!=original: raise
        retrieved=self.request('/storage/v1/object/'+BUCKET+'/'+path,raw=True)
        if digest(retrieved)!=sha or len(retrieved)!=len(original): raise Hold('Private artifact readback differs.')
        return self.rpc('artifact',dict(id=identity(task,'artifact:'+sha),name=name,sha256=sha,byte_count=len(original),storage_path=path,media_type='application/octet-stream',provenance=provenance or dict(version='platform-original-v1')))

    def event(self, kind, message, details=None):
        return self.rpc('event',dict(id=str(uuid.uuid4()),kind=kind,message=message[:4000],details=details or {}))

    def checkpoint(self, status, reason, checkpoint):
        return self.rpc('checkpoint',dict(status=status,reason=reason[:4000],checkpoint=checkpoint))
