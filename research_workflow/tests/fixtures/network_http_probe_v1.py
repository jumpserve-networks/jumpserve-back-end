"""Development example: response-header latency to an operator-supplied HTTP URL.

This measures a request through receipt of response headers, not TCP RTT or an
exact first-byte timestamp. No HTTP observation is retried. It is not a complete
CDN regional-demand experiment (no origin population, warming or VPN control).
"""
import datetime as dt
import json
import os
from pathlib import Path
import time
import urllib.request

spec=json.loads(Path(os.environ['JUMPSERVE_SPEC']).read_text())
parameters=spec['config']['parameters'];url=parameters['url'];count=parameters['requests']
if type(count) is not int or not 1<=count<=20:raise ValueError('Choose 1–20 requests.')
rows=[]
for index in range(count):
    started=dt.datetime.now(dt.timezone.utc).isoformat();tick=time.perf_counter_ns()
    try:
        with urllib.request.urlopen(url,timeout=2) as response:
            elapsed=(time.perf_counter_ns()-tick)/1000000
            status=response.status;response.read(1024)
        rows.append(dict(observation_key=f'request-{index}',metric='response_headers_ms',units='ms',value=elapsed,status='recorded',reason=None,
            metadata=dict(request_started_at=started,http_status=status)))
    except Exception as error:
        rows.append(dict(observation_key=f'request-{index}',metric='response_headers_ms',units='ms',value=None,status='missing',reason=type(error).__name__,metadata=dict(request_started_at=started)))
Path(os.environ['JUMPSERVE_OUTPUT']).write_text(json.dumps(dict(version='network-observations-v1',observations=rows,
    provenance=dict(producer='network-http-probe-v1',request_retries=0,measurement='Request start through response headers',scope='Operator-specified endpoint; development example'),usage=None),sort_keys=True)+'\n')
print('Requests attempted:',count)
