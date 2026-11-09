"""Bounded, append-only follow-ups for the nine unresolved IPv6 claims.

Inventory is an exploratory retrieval pilot, not an experiment or validation.
No arbitrary URLs or code execution, credentials, infrastructure or network probes.
"""
import argparse
import csv
import datetime as dt
import hashlib
import gzip
import io
import json
import math
from pathlib import Path
import platform
import re
import shutil
import ssl
import statistics
import sys
import tarfile
import time
import uuid
from collections import Counter, defaultdict
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'followups' / '20261007-v1'
BASE = 'https://data.measurement.network/dns-mtu-msmt/'
STUDY = '76c70fd9-0217-464e-8c26-0a47832409a3'
NS = uuid.UUID('70f2b6b8-e470-48d4-a6d1-1a679705b1e0')
TARGETS = {
 'rq4-causality': ('43f95edb-31bb-5426-80c0-bf4f4ffc83a0', '39587a40-2572-51ec-a41b-855677448b8e', '5c010a86-fb36-5f0e-96be-99be0831c53a', '0e22ea3c-ef90-58e8-b692-c417637cecf9', 'inconclusive'),
 'rq5-dnssec': ('7ccd1b76-fefa-5715-9777-07ffb5f81473', '7240be07-ee3d-5ed5-ba18-e174becf5aaa', 'bf324893-3aa8-53ea-aace-d1da5a5462fd', 'dd3d0c84-8ea4-50a4-be7f-9382b88115ad', 'inconclusive'),
 'readiness-weighting': ('3a7818ad-0990-5947-ad33-f9db5413b009', '9c7fd82e-9727-5cde-a9b6-9207d63013fc', 'b05fec73-f61b-5e3c-9348-1df7830f44b2', '685566b4-c179-58ee-8b85-ffb3ef0eb561', 'inconclusive'),
 'rfc7872': ('d8e3f2ef-f6f4-5acd-a1fd-5bf00ce7c9d7', '3c71e812-f62b-54ec-9094-e25c81620568', '88c68abb-2827-5166-b7cd-10e6bcee1b83', '9f4709e3-f25f-5b53-b3e7-afb6f6443153', 'inconclusive'),
 'policy-recommendation': ('a769fc0a-b6f4-518b-b1f4-27d7ab79a285', '1c810940-029d-5e09-b23f-1819cf8c3a5e', '877ecb54-57c6-5b40-8357-0ba1eb850749', 'cb1eda7a-cc72-5c11-8b0c-f54d88f22e49', 'inconclusive'),
 'apnic': ('5adfd068-4145-5c74-993a-1aa9487b6edb', '0091543c-43d3-54af-8519-3f305da22133', '2269312c-d9a5-5524-8180-fbf59053d7c8', '8df31c0f-e8d4-5c4a-9682-744e89b2ebe8', 'untested'),
 'packet-sizes': ('8dbd2c75-eba1-5e14-8a5c-8348981fe393', 'edd0ebf4-6afa-578c-aa72-c3faf5150b5c', 'b144c3b1-21d9-5dd5-9c38-c75925b818fd', '3679644b-438c-5e1a-b668-1f5a37555f3e', 'untested'),
 'route-validation': ('86870973-85df-5fb2-a4f7-d6bff5e3a94f', '0c552e80-358c-5f43-97b4-e7533fd27f0f', '383d919e-55ea-5208-82fd-aa3835a9aad0', 'f0504c04-6251-5ac5-a6f1-94bdcab35eac', 'untested'),
 'relative-figures': ('03283d34-2f8f-5dad-9ba8-7d052e1e1229', 'ac674cd6-f50f-5ce2-b49f-d778379fdb8c', '504c73fd-e28c-551d-97bc-130c1ca56644', '536b1a94-4231-5d65-96da-0b39a16eb624', 'untested'),
}
METRICS = ['TIMEOUT', 'SERVFAIL', 'edns0_fallback', 'tcp_fallback', 'udp_fragments', 'udp_packet_too_big']
MTUS = ['mtu1500', 'mtu1280-pmtud', 'mtu1280-nopmtud', 'mtu1280onpath-nopmtud']
EPOCHS = {'tcp-error': ('2025-04-10', '2025-05-23'), 'pre-lgi': ('2025-05-25', '2025-06-14'), 'current': ('2025-06-25', '2025-09-14')}
REVIEWER = dict(type='AI', identity='Codex primary assistant', independence='Same implementer and evaluator; no independent or human review')

def ident(name):
    return str(uuid.uuid5(NS, name))

def workflow_modules():
    sys.path.insert(0, str(ROOT.parents[1] / 'research_workflow'))
    import workflow
    return workflow

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)

def save(path, data):
    write(path, (json.dumps(data, indent=2, allow_nan=False) + '\n').encode())

def inventory(attempt=1, detail=False):
    result = dict(version=1, stage='exploratory-pilot', started_at=stamp(),
                  bounds=dict(urls=7, bytes_per_url=500000, timeout_seconds=12,
                              requests_per_url=1, wall_seconds=100), records=[],
                  limitations='Directory listings are not reviewed measurement inputs; later or undated files cannot silently substitute for original campaign data.')
    tick = time.monotonic()
    names = ('atlas/path_data', 'atlas/results', 'atlas/state', 'atlas-apnic/results',
             'nssets-out/202505', 'ns-list-out/202505', 'raw_data/2025-05-01') if detail else (
             'atlas', 'atlas-apnic', 'nssets-out', 'ns-list-out', 'domainlist-in', 'raw_data', 'processing')
    for name in names:
        url = BASE + name + '/'
        row = dict(url=url, requested_at=stamp(), status='unavailable', sha256=None, byte_count=None, links=[])
        try:
            if time.monotonic() - tick > 100:
                raise ValueError('Pilot wall budget reached')
            # macOS system trust store; verification remains enabled. Attempt 2
            # records the default Python CA-store failure separately.
            context = ssl.create_default_context(cafile='/etc/ssl/cert.pem')
            with urllib.request.urlopen(url, timeout=12, context=context) as response:
                raw = response.read(500001)
                if len(raw) > 500000:
                    raise ValueError('Listing exceeds the 500 KB bound; bytes are not presented as a complete inventory')
                row.update(status='retrieved', http_status=response.status, retrieved_url=response.url,
                           sha256=sha(raw), byte_count=len(raw))
            write(OUT / ('pilot-detail' if detail else 'pilot-attempt' + str(attempt)) / (name.replace('/', '_') + '.html'), raw)
            row['links'] = [urllib.parse.urljoin(url, href) for href in re.findall(r'href="([^"]+)"', raw.decode('utf-8', errors='replace')) if href not in ('../', '/')]
        except (urllib.error.URLError, ValueError) as error:
            row['reason'] = str(error)
        row['ended_at'] = stamp()
        result['records'].append(row)
    result.update(ended_at=stamp(), wall_seconds=time.monotonic() - tick,
                  original_bytes=sum(r['byte_count'] or 0 for r in result['records']),
                  measured_charges_usd=None, model_usage=None,
                  unallocated_costs=['Local execution, network transfer and Codex; missing usage is not zero'])
    result['attempt'] = attempt
    save(OUT / ('pilot-detail.json' if detail else 'pilot-inventory' + ('' if attempt == 1 else '-attempt' + str(attempt)) + '.json'), result)
    print(json.dumps({**result, 'records': [{**r, 'links': r['links'][:20], 'link_count': len(r['links'])} for r in result['records']]}, indent=2))

def recover_detail():
    names = ('atlas/path_data', 'atlas/results', 'atlas/state', 'atlas-apnic/results',
             'nssets-out/202505', 'ns-list-out/202505', 'raw_data/2025-05-01')
    result = dict(stage='exploratory-pilot', recovered_at=stamp(), records=[],
                  failed_execution='inventory-detail retrieved seven originals, then failed saving metadata: FileExistsError because the report path collided with its raw-byte directory. The original files are preserved. Retrieval timestamps and response metadata were lost; this recovery does not invent them.')
    for name in names:
        path = OUT / 'pilot-detail' / (name.replace('/', '_') + '.html')
        raw = path.read_bytes()
        result['records'].append(dict(url=BASE + name + '/', status='retrieved',
            sha256=sha(raw), byte_count=len(raw), path=str(path.relative_to(OUT)),
            retrieval_timestamp=None, http_status=None, retrieved_url=None,
            links=[urllib.parse.urljoin(BASE + name + '/', v) for v in re.findall(r'href="([^"]+)"', raw.decode()) if v != '../']))
    save(OUT / 'pilot-detail-recovery.json', result)
    print(json.dumps({**result, 'records': [{**r, 'links': r['links'][:5], 'link_count': len(r['links'])} for r in result['records']]}, indent=2))

def retrieve_samples():
    result = dict(stage='exploratory-pilot', started_at=stamp(), records=[],
                  bounds=dict(bytes_per_url=1000000, timeout_seconds=15, requests=3),
                  limitations='Two sentinel route-summary files inspect the schema; this is not a route-coverage census. No interpretation before freezing the main protocol.')
    for name in ('atlas/results/2025-05-25.json', 'atlas/results/2025-06-25.json', 'raw_data/2025-05-01/in-data-mtu1500/'):
        row = dict(url=BASE + name, requested_at=stamp(), status='unavailable', sha256=None, byte_count=None)
        try:
            with urllib.request.urlopen(row['url'], timeout=15, context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')) as response:
                raw = response.read(1000001)
                if len(raw) > 1000000:
                    raise ValueError('Exceeded the 1 MB pilot file bound; not a complete input')
                row.update(status='retrieved', http_status=response.status, retrieved_url=response.url,
                           sha256=sha(raw), byte_count=len(raw))
            path = 'pilot-samples/' + name.rstrip('/').replace('/', '_') + ('.html' if name.endswith('/') else '')
            write(OUT / path, raw)
            row['path'] = path
        except (urllib.error.URLError, ValueError) as error:
            row['reason'] = str(error)
        row['ended_at'] = stamp()
        result['records'].append(row)
    result['ended_at'] = stamp()
    save(OUT / 'pilot-samples.json', result)
    print(json.dumps(result, indent=2))

def prepare_inputs():
    """Preserve original member bytes in a deterministic, compressed container."""
    members = [(str(p.relative_to(ROOT)), p) for p in sorted((ROOT / 'raw/archive').glob('*/dns.json.gz'))]
    members += [(str(p.relative_to(ROOT)), p) for p in sorted((ROOT / 'raw/archive').glob('*/pcap.json'))]
    members += [(str(p.relative_to(ROOT)), p) for p in [ROOT / 'raw/main.pdf', ROOT / 'raw/literature/37-0.bin', ROOT / 'evidence/archive-retrieval-v2.json']]
    author = ROOT / 'author/srv/script_backup/crontrol-hosts/backend.dns-mtu.measurement.network/opt/msmt/msmt/plot'
    members += [(str(p.relative_to(ROOT)), p) for p in [author / 'plot_overview_absolute.py', author / 'plot_overview_rel_1500_v4.py']]
    members += [('followup/' + str(p.relative_to(OUT)), p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.suffix in ('.json', '.html')]
    manifest = []
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name, path in sorted(members):
                raw = path.read_bytes()
                manifest.append(dict(path=name, sha256=sha(raw), byte_count=len(raw)))
                info = tarfile.TarInfo(name)
                info.size = len(raw); info.mtime = 0
                archive.addfile(info, io.BytesIO(raw))
    raw = buffer.getvalue()
    if len(raw) > 10000000 or sum(m['byte_count'] for m in manifest) > 60000000:
        raise ValueError('Prepared originals exceed the declared compressed or expanded input budget')
    write(OUT / 'inputs.tar.gz', raw)
    save(OUT / 'input-manifest.json', dict(version=1, created_at=stamp(), sha256=sha(raw), byte_count=len(raw),
         transformation='Lossless deterministic tar/gzip container; every member retains its original bytes and independent hash.',
         original_bytes=sum(m['byte_count'] for m in manifest), members=manifest))
    print(json.dumps(dict(container_bytes=len(raw), original_bytes=sum(m['byte_count'] for m in manifest), members=len(manifest), sha256=sha(raw))))

def freeze():
    workflow = workflow_modules()
    manifest = json.loads((OUT / 'input-manifest.json').read_bytes())
    records = []
    for key, (claim_id, parent, _, _, _) in TARGETS.items():
        doc = dict(
          questions=[f'What additional archived-data check is feasible for {key}, and what remains unresolved?', 'Do newly examined printed difference cells match independently folded historical counts?'],
          hypotheses='Printed arithmetic may agree without identifying a causal effect, a global population estimate, or operational effectiveness. Missing prerequisites exclude the requested empirical test; a source-availability audit is not that test.',
          metrics=[dict(name=m, units='percentage points', tolerance_absolute=0.0051) for m in METRICS] + [dict(name='route_count_conservation', units='traces'), dict(name='path_classification_consistency', units='traces')],
          configurations=[dict(identity=key + '-followup-v1', details=dict(claim_id=claim_id, epochs=EPOCHS, families=['v4-only', 'v6-only'], edns_bytes=[1232, 4096], mtus=MTUS, cohorts=['dnssec', 'no-dnssec'], upstreams=['lgi', 'no-lgi'], route_sentinels=['2025-05-25', '2025-06-25']))],
          input_versions=[dict(identity='inputs.tar.gz', sha256=manifest['sha256']), dict(identity='input-manifest.json', sha256=sha((OUT / 'input-manifest.json').read_bytes()))],
          dates_or_epochs='Historical 2025 dates as enumerated; three original fold windows and two preselected route-summary sentinel days. Retrieval in October 2026 is not a new measurement epoch.',
          algorithms=['Independent Python count validation, daily percentages and unweighted daily means; do not reuse previous numerical outputs.', 'Figures13–15c: DNSSEC minus NoDNSSEC; d: LGI minus NoLGI; Figure16a/b: pre-LGI minus TCP-error. All are percentage-point subtraction, not division.', 'Parse four 16-row 12-column blocks on physical PDF pages17–19 and two on page20. Axes verified in rendered pages and extraction labels. c groups: v4/LGI, v6/LGI, v4/NoLGI, v6/NoLGI; d groups: v4/NoDNSSEC, v4/DNSSEC, v6/NoDNSSEC, v6/DNSSEC.', 'Route audit: independently sum four recorded classes and every class path multiplicity; compare AS6830 token presence and destination-AS211286 presence with recorded classification. Ambiguous paths retain their ambiguity.'],
          seeds=dict(applicable=False, rationale='Deterministic census of the declared cells and two chosen archived days; no randomized allocation or sampling.'),
          controls=['Rehash container and every expanded original member; reject unexpected or duplicate member identities.', 'Validate finite nonnegative integer counts, positive denominators, status conservation, exact configuration/day matching; no zero imputation.', 'Printed absolute panels supply a subtraction-only cross-check with 0.01 pp rounding bound, separate from raw-count arithmetic. Both share the same PDF and upstream data.', 'Check matched-day subtraction as sensitivity to subtracting means with unequal valid-day coverage; mark cross-epoch Figure16 pairing inapplicable.'],
          sensitivity=dict(applicable=True, rationale='Compare difference of means with mean of differences over the exact intersection of valid dates. Two sentinel route days are descriptive, not representative sample replications.'),
          replication_unit='Historical daily panel for percentages; entire configuration-matched epochs for Figure16. Individual cells, days, repeated paths and servers are not independent study replications.',
          sample_size_rationale='Census of 1152 difference cells in Figures13–15c/d and 384 cells in Figure16a/b. Two route dates chosen before interpretation to match the start of original before/after windows; no power or random-sample claim.',
          exclusions='Dates outside the three original fold windows are excluded. Missing files/counts and invalid denominators are retained. No complete route census, APNIC campaign, packet-distribution experiment or population weighting is claimed.',
          missing_data='Missing, invalid and ambiguous observations retain null values with reasons. Recorded zeros remain numeric zero. Unavailable full inputs exclude the substantive experiment and retain its source audit.',
          statistics='Descriptive means, paired contrasts, counts and numerical agreement under absolute 0.0051 pp criterion. The subtraction-only PDF control uses <=0.01 pp to allow rounding of both operands. No inferential confidence intervals.',
          dependence='Same servers, changing NS-set panels, repeated days and route probes are correlated. These are convenience-panel archives. No causal, geographic, operational or population inference follows from agreement.',
          resource_limits=dict(max_observations=10000, max_input_bytes=10000000, max_expanded_bytes=60000000, wall_seconds=300, new_network_hosts=0, model_calls=0, estimated_cost_usd=None, cost_basis='Local compute, database/storage/transfer and Codex are unallocated; missing charges are not zero.'),
          stopping_rule='One main attempt per frozen version. Stop on changed hashes, malformed topology, count invariant or resource limit; preserve failures and amend prospectively. Inputs too large or unavailable exclude their experiment, rather than substituting data.',
          prior_exposure='Previous absolute-panel results and nine original labels were known. This turn inspected directory schemas, two route-summary schemas, relative panel axes and Figure16 contrast labels before freezing. Some sentinel counts were visible in schema previews. This is a follow-up with prior exposure, not independent held-out validation.',
          coverage_limits='Partial claim assessment only. A successful source audit is not successful reproduction. No raw PCAP replay, route-to-DNS-query join, independent IP-to-AS reclassification, representative population sample, historical matched RFC experiment or multi-operator operational intervention.',
          data_origin='archived-data')
        doc['applicability'] = dict(
            relative_cell_comparison=key in ('relative-figures', 'rq5-dnssec', 'rq4-causality'),
            route_summary_checks=key == 'route-validation',
            full_requested_scope='Inapplicable to the available reduced archive for this attempt; excluded prerequisites are recorded separately, not invented.',
            historical_rfc_source_check=key == 'rfc7872',
            other_metrics='Declared numeric metrics are inapplicable to a source-availability-only run; such runs contain no fabricated measurements.')
        if key == 'rfc7872':
            doc['algorithms'].append('Compare six quoted fragment-loss percentages with RFC7872 Tables1–2; independently recompute four paper Section7.4 percentages from its stated integer numerators/denominators, using the same 0.0051 pp printing criterion. This is source arithmetic, not repeated network measurements.')
        protocol = workflow.protocol_record(doc, ident(key + ':protocol'))
        records.extend([
          dict(kind='protocols', record=protocol),
          dict(kind='configurations', record=dict(id=ident(key + ':configuration'), identity=key + '-followup-v1', details=doc['configurations'][0]['details'], input_versions=doc['input_versions'], requested_resources=doc['resource_limits'])),
          dict(kind='campaigns', record=dict(id=ident(key + ':campaign'), protocol_id=protocol['id'], followup_of=parent, title='Unresolved claim follow-up: ' + key, stage='follow-up', experiment_type='reanalysis' if key in ('relative-figures', 'rq5-dnssec', 'rq4-causality', 'route-validation') else 'source-review', adapter='domain-operator', planned_units=1536 if key == 'relative-figures' else 1, coverage_limits=doc['coverage_limits'])),
          dict(kind='claim_checks', record=dict(id=ident(key + ':check'), claim_id=claim_id, campaign_id=ident(key + ':campaign'), method='Bounded archived-data reanalysis and original-source prerequisite audit', applicability='partial', rationale='The original requested empirical scope is attempted only when its exact prerequisites are present; exclusions remain explicit.'))])
    for row in records:
        workflow.validate_record(row['kind'], row['record'])
    save(OUT / 'registration.json', dict(version=1, study_id=STUDY, frozen_at=stamp(), records=records))
    print(json.dumps(dict(protocols=9, campaigns=9, frozen=True, registration_sha256=sha((OUT / 'registration.json').read_bytes()))))

def load_inputs():
    manifest = json.loads((OUT / 'input-manifest.json').read_bytes())
    raw = (OUT / 'inputs.tar.gz').read_bytes()
    if len(raw) > 10000000 or sha(raw) != manifest['sha256']:
        raise ValueError('Original input container identity differs')
    expected = {m['path']: m for m in manifest['members']}; data = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for member in archive:
            if not member.isfile() or member.name not in expected or member.name in data:
                raise ValueError('Unexpected, non-file or duplicate input member')
            m = expected[member.name]
            if member.size != m['byte_count']:
                raise ValueError('Original member size differs')
            original = archive.extractfile(member).read()
            if sha(original) != m['sha256']:
                raise ValueError('Original member hash differs')
            data[member.name] = original
    if set(data) != set(expected) or sum(len(v) for v in data.values()) > 60000000:
        raise ValueError('Original member coverage or expanded-byte bound differs')
    return manifest, data

def count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0

def rate(n, denominator, metric):
    if n is None or denominator is None:
        return dict(value=None, status='missing', reason='Recorded numerator or denominator absent')
    if not count(n) or not count(denominator) or denominator == 0 or (n > denominator and metric != 'tcp_fallback'):
        return dict(value=None, status='invalid', reason='Invalid count, zero denominator or numerator outside a proportion bound')
    return dict(value=100 * n / denominator, status='recorded', reason=None)

def daily_folds(data):
    """Fresh direct parsing; neither previous outputs nor author functions used."""
    grouped = defaultdict(dict); checks = Counter(); missing_dates = []
    for epoch, (lo, hi) in EPOCHS.items():
        date = dt.date.fromisoformat(lo)
        while date <= dt.date.fromisoformat(hi):
            day = date.isoformat(); parsed = {}
            for kind in ('dns', 'pcap'):
                path = f'raw/archive/{day}/' + ('dns.json.gz' if kind == 'dns' else 'pcap.json')
                raw = data.get(path)
                if raw is not None:
                    parsed[kind] = json.loads(gzip.decompress(raw) if kind == 'dns' else raw)
                else:
                    missing_dates.append(dict(date=day, kind=kind, reason='Original historical input unavailable'))
            for family in ('v4-only', 'v6-only'):
                for cohort in ('dnssec', 'no-dnssec'):
                    for upstream in ('lgi', 'no-lgi'):
                        for mtu in MTUS:
                            for edns in (1232, 4096):
                                case = f'{mtu}{"-lgi" if upstream == "lgi" else ""}-{family}-edns{edns}'
                                s = parsed.get('dns', {}).get(cohort, {}).get(case, {})
                                p = parsed.get('pcap', {}).get(cohort.replace('-', '_'), {}).get(case, {}).get(family[:2], {})
                                if s:
                                    vals = list(s.get('status', {}).values())
                                    if not count(s.get('count')) or not all(count(v) for v in vals) or sum(vals) != s['count']:
                                        raise ValueError(f'DNS status conservation failed: {day} {cohort} {case}')
                                    checks['dns_status_conservation'] += 1
                                for metric in METRICS:
                                    if metric in ('TIMEOUT', 'SERVFAIL'):
                                        n = s.get('status', {}).get(metric); den = s.get('count')
                                    else:
                                        part = p.get('TCP' if metric == 'tcp_fallback' else 'UDP', {})
                                        field = dict(edns0_fallback='client_edns_fallback', tcp_fallback='count', udp_fragments='frag', udp_packet_too_big='packet_too_big')[metric]
                                        n = part.get(field); den = p.get('UDP', {}).get('count')
                                    grouped[(epoch, family, cohort, upstream, mtu, edns, metric)][day] = dict(numerator=n, denominator=den, **rate(n, den, metric))
            date += dt.timedelta(days=1)
    return grouped, checks, missing_dates

def blocks(page):
    result = []; current = []
    for line in page.extract_text().splitlines():
        if re.fullmatch(r'(?:-?\d+\.\d{2}\s*){12}', line.strip()):
            current.append([float(v) for v in line.split()])
        elif current:
            result.append(current); current = []
    if current:
        result.append(current)
    if any(len(b) != 16 for b in result):
        raise ValueError('PDF printed numerical block topology differs')
    return result

def compare_relative(data, grouped):
    from pypdf import PdfReader
    paper = PdfReader(io.BytesIO(data['raw/main.pdf']))
    absolute = {}; specifications = []
    for page, figure, epoch in ((17, 13, 'pre-lgi'), (18, 14, 'current'), (19, 15, 'tcp-error')):
        values = blocks(paper.pages[page - 1])
        if len(values) != 4:
            raise ValueError('Expected exactly four heatmap panels')
        for panel, family in enumerate(('v4-only', 'v6-only')):
            for ri, row in enumerate(values[panel]):
                upstream, cohort = [('lgi', 'no-dnssec'), ('lgi', 'dnssec'), ('no-lgi', 'no-dnssec'), ('no-lgi', 'dnssec')][ri // 4]
                for ci, value in enumerate(row):
                    absolute[(epoch, family, cohort, upstream, MTUS[ri % 4], [1232, 4096][ci // 6], METRICS[ci % 6])] = value
        for panel in ('c', 'd'):
            for ri, row in enumerate(values[2 if panel == 'c' else 3]):
                if panel == 'c':
                    family, upstream = [('v4-only', 'lgi'), ('v6-only', 'lgi'), ('v4-only', 'no-lgi'), ('v6-only', 'no-lgi')][ri // 4]
                    left = (epoch, family, 'dnssec', upstream); right = (epoch, family, 'no-dnssec', upstream)
                else:
                    family, cohort = [('v4-only', 'no-dnssec'), ('v4-only', 'dnssec'), ('v6-only', 'no-dnssec'), ('v6-only', 'dnssec')][ri // 4]
                    left = (epoch, family, cohort, 'lgi'); right = (epoch, family, cohort, 'no-lgi')
                for ci, value in enumerate(row):
                    suffix = (MTUS[ri % 4], [1232, 4096][ci // 6], METRICS[ci % 6])
                    specifications.append(dict(observation_id=f'fig{figure}{panel}-{ri}-{ci}', location=f'Physical PDF page{page}, Figure{figure}{panel}, row{ri + 1}, column{ci + 1}', published=value, left=left + suffix, right=right + suffix))
    values = blocks(paper.pages[19])
    if len(values) != 2:
        raise ValueError('Expected exactly two Figure16 difference panels')
    for panel, family in enumerate(('v4-only', 'v6-only')):
        for ri, row in enumerate(values[panel]):
            upstream, cohort = [('lgi', 'no-dnssec'), ('lgi', 'dnssec'), ('no-lgi', 'no-dnssec'), ('no-lgi', 'dnssec')][ri // 4]
            for ci, value in enumerate(row):
                suffix = (family, cohort, upstream, MTUS[ri % 4], [1232, 4096][ci // 6], METRICS[ci % 6])
                specifications.append(dict(observation_id=f'fig16{"ab"[panel]}-{ri}-{ci}', location=f'Physical PDF page20, Figure16{"ab"[panel]}, row{ri + 1}, column{ci + 1}', published=value, left=('pre-lgi',) + suffix, right=('tcp-error',) + suffix))
    results = []
    for row in specifications:
        populations = [grouped[tuple(row[k])] for k in ('left', 'right')]
        valid = [{day: r['value'] for day, r in p.items() if r['status'] == 'recorded'} for p in populations]
        means = [statistics.fmean(v.values()) if v else None for v in valid]
        value = means[0] - means[1] if None not in means else None
        delta = value - row['published'] if value is not None else None
        paired = sorted(set(valid[0]) & set(valid[1])) if row['left'][0] == row['right'][0] else None
        paired_mean = statistics.fmean(valid[0][d] - valid[1][d] for d in paired) if paired else None
        printed_control = absolute[tuple(row['left'])] - absolute[tuple(row['right'])] - row['published']
        results.append(dict(**row, value=value, delta_pp=delta, assessment='inconclusive' if delta is None else 'reproduced' if abs(delta) <= 0.0051 else 'discrepant',
            metric=row['left'][-1], units='percentage points', planned_days=[len(p) for p in populations], valid_days=[len(v) for v in valid],
            input_status_counts=[dict(Counter(r['status'] for r in p.values())) for p in populations],
            paired_days=len(paired) if paired is not None else None, paired_mean_pp=paired_mean,
            pairing_inapplicable_reason='Different epochs cannot be paired by date' if paired is None else None,
            paired_sensitivity_difference_pp=paired_mean - value if paired_mean is not None and value is not None else None,
            printed_absolute_subtraction_delta_pp=printed_control, printed_control_agrees=abs(printed_control) <= 0.0100001))
    if len(results) != 1536 or len({r['observation_id'] for r in results}) != 1536:
        raise ValueError('Expected 1536 uniquely identified printed difference cells')
    return results

def route_checks(data):
    results = []
    for day in ('2025-05-25', '2025-06-25'):
        path = f'followup/pilot-samples/atlas_results_{day}.json'
        raw = data[path]; document = json.loads(raw)
        if set(document['data']) != {'93031095', '93031096', '93031097', '93031098'}:
            raise ValueError('Route sentinel measurement identifiers differ')
        for measurement, row in document['data'].items():
            classes = {k: row[k] for k in ('lgi', 'lgi_zrh', 'no_of', 'no_lgi')}
            totals = {k: sum(v.values()) for k, v in row['paths'].items()}
            if not count(row['total']) or not all(count(v) for v in classes.values()) or set(totals) != set(classes):
                raise ValueError('Invalid route summary topology or counts')
            conservation = sum(classes.values()) == row['total']
            class_checks = {k: totals[k] == v for k, v in classes.items()}
            # Token presence is only a sanity check of the already mapped AS paths;
            # mapping correctness and the author's Zurich rule are not assumed.
            token_presence = {}; unknown = {}
            for group, paths in row['paths'].items():
                if not all(count(v) for v in paths.values()):
                    raise ValueError('Invalid path multiplicity')
                token_presence[group] = sum(n for path, n in paths.items() if '6830' in path.split())
                unknown[group] = sum(n for path, n in paths.items() if '*' in path.split() or 'not_found' in path.split())
            results.append(dict(date=day, measurement_id=measurement, trace_info=document['trace_info'][measurement], original_sha256=sha(raw),
                recorded_classes=classes, total=row['total'], class_sum=sum(classes.values()), path_class_sums=totals,
                conservation=conservation, path_class_conservation=class_checks, as6830_token_counts=token_presence,
                paths_with_unknown_hops_counts=unknown, as6830_recorded_fraction_percent=100 * (classes['lgi'] + classes['lgi_zrh']) / row['total'] if row['total'] else None,
                limitation='Archived author-AS-mapped path summaries only; no independent raw-trace/IP-to-AS replay or every-DNS-query route validation. Unknown-hop counts overlap recorded classes and are not additional trials.'))
    return results

def rfc_checks(data):
    from pypdf import PdfReader
    paper = PdfReader(io.BytesIO(data['raw/main.pdf']))
    pages = [(i + 1, p.extract_text()) for i, p in enumerate(paper.pages)]
    page, excerpt = next((i, t) for i, t in pages if '7.4' in t and '30.51%' in t)
    rfc = data['raw/literature/37-0.bin'].decode()
    rows = []
    for n, expected in enumerate((30.51, 39.17, 38.55, 28.26, 35.68, 55.23)):
        token = f'{expected:.2f}%'
        if token not in rfc or token not in excerpt:
            raise ValueError('Quoted RFC percentage not found in both exact sources')
        rows.append(dict(observation_id='rfc-quoted-' + str(n), value=expected, published=expected, delta_pp=0.0,
                         units='percent', metric='fragment_drop_rate', location=f'Paper physical PDF page{page}, Section7.4; RFC7872 Table{1 if n < 3 else 2}, FH512 column', assessment='reproduced',
                         limitation='Source quotation check, not a historical experiment or same-population trend comparison'))
    for key, numerator, denominator, expected in (('mail-drop', 315, 1425, 22.11), ('mail-transit', 105, 315, 33.34), ('name-drop', 2406, 8245, 29.18), ('name-transit', 1280, 2406, 53.20)):
        if f'{expected:.2f}%' not in excerpt:
            raise ValueError('Published arithmetic target absent from specified paper section')
        value = 100 * numerator / denominator
        rows.append(dict(observation_id=key, numerator=numerator, denominator=denominator, value=value, published=expected,
            delta_pp=value - expected, units='percent', metric='fragment_drop_rate' if key.endswith('drop') else 'transit_share_of_drops', location=f'Paper physical PDF page{page}, Section7.4',
            assessment='reproduced' if abs(value - expected) <= 0.0051 else 'discrepant',
            limitation='Arithmetic from published counts only; no new observed packets. Printed counts and denominators share the paper source.'))
    return rows

def run():
    workflow = workflow_modules()
    registration = json.loads((OUT / 'registration.json').read_bytes())
    for entry in registration['records']:
        workflow.validate_record(entry['kind'], entry['record'])
    started = stamp(); tick = time.monotonic()
    manifest, data = load_inputs()
    protocols = [e['record'] for e in registration['records'] if e['kind'] == 'protocols']
    if any(p['document']['input_versions'][0]['sha256'] != manifest['sha256'] for p in protocols):
        raise ValueError('Frozen protocol input identity differs')
    grouped, invariants, missing_dates = daily_folds(data)
    relative = compare_relative(data, grouped)
    routes = route_checks(data)
    rfc = rfc_checks(data)
    elapsed = time.monotonic() - tick
    if elapsed > 300:
        raise ValueError('Main campaign wall budget exceeded')
    result = dict(version=1, stage='follow-up-main', started_at=started, ended_at=stamp(), execution_revision=sha(Path(__file__).read_bytes()),
         protocol_registration_sha256=sha((OUT / 'registration.json').read_bytes()), input_sha256=manifest['sha256'],
         source_members_verified=len(data), original_bytes_verified=sum(len(v) for v in data.values()),
         relative_cells=relative, route_sentinels=routes, rfc_source_checks=rfc, missing_original_inputs=missing_dates,
         count_invariants=dict(invariants), wall_seconds=elapsed, python_version=platform.python_version(),
         summary=dict(relative_cells=len(relative), relative_labels=dict(Counter(r['assessment'] for r in relative)),
            max_absolute_relative_difference_pp=max(abs(r['delta_pp']) for r in relative if r['delta_pp'] is not None),
            printed_subtraction_control_disagreements=sum(not r['printed_control_agrees'] for r in relative),
            matched_day_sensitivity_changes=sum(abs(r['paired_sensitivity_difference_pp'] or 0) > 1e-9 for r in relative),
            route_sentinels=len(routes), route_total_traces=sum(r['total'] for r in routes),
            route_conservation_failures=sum(not r['conservation'] or not all(r['path_class_conservation'].values()) for r in routes),
            rfc_source_labels=dict(Counter(r['assessment'] for r in rfc))),
         usage=dict(measured_charges_usd=None, estimated_charges_usd=None, model_usage=None,
             requested_new_network_hosts=0, requested_model_calls=0,
             unallocated_costs=['Local CPU/memory, Supabase storage/database/transfer, Codex and documentation retrieval']),
         reviewer=REVIEWER,
         limitations='No independent or human review. Numerical agreement and source arithmetic do not validate causality, global population coverage, raw packet parsing, classifier correctness, or operational recommendations.')
    save(OUT / 'results.json', result)
    print(json.dumps({k: result[k] for k in ('started_at', 'ended_at', 'summary', 'count_invariants', 'wall_seconds', 'usage')}, indent=2))

def build_import():
    """Retain original protocols/results; prepare distinct typed database records."""
    workflow = workflow_modules()
    before = json.loads((OUT / 'production-before.json').read_bytes())
    result = json.loads((OUT / 'results.json').read_bytes())
    registration = json.loads((OUT / 'registration.json').read_bytes())
    records = registration['records']
    # The database expresses a partial check as applicable plus a scoped rationale;
    # it does not have a 'partial' applicability enum. This serialization correction
    # changes no frozen protocol, data, algorithm or original result.
    for entry in records:
        if entry['kind'] == 'claim_checks':
            entry['record']['applicability'] = 'applicable'
            entry['record']['rationale'] += ' Applicability applies only to this bounded partial check; excluded full-scope experiments remain uncovered.'
    sources = [dict(key='paper', citation='Tobias Fiebig and Anja Feldmann. How I learned to stop worrying and love IPv6. IMC2025. DOI10.1145/3730567.3764439.',
        source_url='https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf', path='raw/main.pdf', kind='research',
        examined='Difference heatmap cell topology, axes, labels and all 1536 values in physical PDF pages17–20; Section7.4 source arithmetic; sampling and conclusion context.',
        unexamined='Every other plotted observation and full figure-by-figure review outside these scoped sections; no inference from retrieval alone.', findings='1536 difference cells meet 0.0051 pp; source-only arithmetic 105/315 rounds33.33%, versus printed33.34%. This is not full claim validation.'),
        dict(key='rfc', citation='Gont, Linkova, Chown and Liu. RFC7872. Observations on the Dropping of Packets with IPv6 Extension Headers in the Real World. June2016.',
        source_url='https://www.rfc-editor.org/rfc/rfc7872.txt', path='raw/literature/37-0.bin', kind='standard', examined='Section2 methods and all entries in Tables1–2; fragment-loss quotation comparison.',
        unexamined='Remaining sections and appendices not completely re-reviewed in this follow-up.', findings='Six quoted fragment-loss rates match the original tables. Epoch, population, TCP/ICMP and direction differ; no matched trend validation.')]
    manifest, data = load_inputs()
    for source in sources:
        raw = data[source['path']]
        records.insert(0, dict(kind='sources', record=dict(id=ident('source:' + source['key']), citation=source['citation'], source_url=source['source_url'], retrieved_url=source['source_url'],
            kind=source['kind'], role='Primary source for the bounded follow-up check', retrieved_version='Previously retained original version, verified by SHA256; author manuscript PuRe item3670144_1 for paper.',
            sha256=sha(raw), byte_count=len(raw), access_status='retrieved', review_status='partial-review',
            review_definition='Complete review means substantive examination of the entire resource, including all figures, tables, appendices and supplements. This follow-up is explicitly partial.',
            examined=source['examined'], unexamined=source['unexamined'], retrieval_attempts=[dict(method='Retained original bytes from prior retrieval; container round-trip hash verification', new_network_retrieval=False, original_path=source['path'])],
            reviewer=REVIEWER, findings=source['findings'], limitations='Same AI implementer; no independent/human review. Hash verification does not establish substantive completeness. PDF extraction of unrelated complex pages reached the form-XObject cap; no complete review of those figures is claimed.')))
    missing = {
     'rq4-causality': ('partial', 'Descriptive archived LGI contrasts checked; causal effect remains unidentified.', 'Same-population, route-equal controlled policy crossover with packet-level joins.', ['Contemporaneous BGP/route records', 'Policy intervention timestamps and verified policy', 'Exact packet and query identities', 'Provider/population controls']),
     'rq5-dnssec': ('partial', 'Archived DNSSEC-minus-NoDNSSEC contrast checked; provider and outcome-derived cohort confounding remain.', 'Retrieve original NS-set/domain/provider identities and independently assigned DNSSEC labels; compare within matched strata.', ['Original zone/NS-set identities and weekly versions', 'Independent DNSSEC classification', 'Provider mapping and weighting']),
     'readiness-weighting': ('excluded', 'Domain/user-weighted readiness experiment excluded: required historical mappings were not parsed within this bounded follow-up. Inputs are publicly listed, not proven unavailable.', 'Run a separately budgeted streaming domain/NS-set identity join and prespecified weighting sensitivity.', ['Historical NS-set mapping (May listing52138317 compressed bytes)', 'Historical NS-list mapping (May listing710841258 compressed bytes)', 'Domain sampling inclusion/weight information']),
     'rfc7872': ('partial', 'Source quotations and published-count arithmetic checked; matched historical network comparison remains unresolved.', 'Recover historical target identities and raw traces, then execute a transport/direction/population-matched design.', ['Original 2014/2015 raw traces and target lists', 'Original2025 ICMP targets/traces', 'Comparable probes and endpoint selection']),
     'policy-recommendation': ('excluded', 'Multi-operator operational validation excluded: no resolver/operator intervention or independent operational review was performed.', 'Preregister multiple operators, resolver implementations, query types and operational/security endpoints, preserving IPv4/IPv6 coexistence scope.', ['Participating operators and resolver environments', 'Operational outcome definitions and deployment windows', 'Independent operational/security review']),
     'apnic': ('excluded', 'Original APNIC sampling-weighted global fragment-loss test excluded. Author Atlas-to-APNIC summaries are a distinct dataset and were not substituted.', 'Obtain the original APNIC ad-request observations and sampling weights, with epochs, targets and policies.', ['Original APNIC observations', 'Ad sampling/region weights', 'Fragment policies and target identities']),
     'packet-sizes': ('excluded', 'Packet-size mean/SD reproduction excluded: daily server-count summaries do not contain individual packet lengths or a sufficient length histogram. Full May1 PCAP input/parser replay was not run.', 'Retrieve the exact May1 captures/parser revision under a separate large-input protocol and validate snaplen/offload/packet identity and length units.', ['Full2025-05-01 PCAPs (paperTable1 reports about55GB compressed per daily path campaign)', 'Packet-size parser revision', 'Capture/filter/offload/length definitions']),
     'route-validation': ('partial', 'Counts and mapped-path multiplicities checked for eight measurement/date summaries on two sentinel days. Full route census, probe coverage and DNS-query route validation remain uncovered.', 'Retrieve every declared day and raw Atlas/probe results, verify AS mapping revisions, and join observations by probe/measurement/timestamp.', ['All original raw Atlas/probe observations and results', 'Contemporaneous IP-to-AS and geo mapping versions', 'DNS-query/route timestamp joins']),
     'relative-figures': ('partial', '1536 cells in Figures13–15c/d and16a/b checked. Every time-series/plotted observation in Figures3–12 and17–21 remains outside this campaign.', 'Retrieve/digitize the remaining plots with matched original data and independently verify axes, denominators and plotted point identities.', ['Underlying point data for Figures3–12 and17–21', 'Matched dates/configurations and exact figure revisions', 'Digitization uncertainty where raw point data cannot be recovered']),
    }
    queue_requests = []
    for key, (claim, _, old_job, old_assessment, label) in TARGETS.items():
        status, reason, next_check, required = missing[key]
        run_id = ident(key + ':run')
        if key in ('relative-figures', 'route-validation'):
            label = 'inconclusive'
        records.append(dict(kind='runs', record=dict(id=run_id, campaign_id=ident(key + ':campaign'), configuration_id=ident(key + ':configuration'),
            status=status, reason=reason, started_at=result['started_at'], ended_at=result['ended_at'], analysis_version='ipv6-unresolved-followup-v1',
            input_sha256=manifest['sha256'], output_sha256=sha((OUT / 'results.json').read_bytes()),
            requested_resources=dict(new_network_hosts=0, model_calls=0, wall_seconds=300),
            actual_resources=dict(shared_execution='Nine scoped follow-ups share one local data pass; do not multiply its measured wall time or count them as independent replications.', shared_wall_seconds=result['wall_seconds'], compressed_input_bytes=manifest['byte_count'], original_input_bytes=manifest['original_bytes']),
            usage=result['usage'], provenance=dict(execution_revision=result['execution_revision'], protocol_registration_sha256=result['protocol_registration_sha256'],
                data_origin='archived-data', stage='follow-up', excluded_experiment=status == 'excluded', source_audit_completed=True, reviewer=REVIEWER))))
        selected = result['relative_cells'] if key == 'relative-figures' else [r for r in result['relative_cells'] if (key == 'rq5-dnssec' and re.match('fig1[345]c-', r['observation_id'])) or (key == 'rq4-causality' and re.match('fig1[345]d-', r['observation_id']))] if key in ('rq5-dnssec', 'rq4-causality') else result['rfc_source_checks'] if key == 'rfc7872' else result['route_sentinels'] if key == 'route-validation' else []
        records.append(dict(kind='summaries', record=dict(id=ident(key + ':summary'), run_id=run_id, metric='claim_check_coverage', units='declared checks',
            statistics=dict(completed_scoped_checks=len(selected), full_scope_status=status, reason=reason, results_artifact_sha256=sha((OUT / 'results.json').read_bytes())),
            interval_kind='none', uncertainty_method='No inferential interval: correlated convenience-panel archives and deterministic source audits.',
            planned=1, unexpected=0, recorded=1 if selected else 0, missing=0, invalid=0, ambiguous=0, excluded=0 if selected else 1)))
        evidence = [dict(kind='runs', id=run_id)]
        if key in ('rq4-causality', 'rq5-dnssec'):
            evidence.append(dict(kind='runs', id=ident('relative-figures:run'), relationship='Shared inputs and execution; not independent evidence'))
        records.append(dict(kind='assessments', record=dict(id=ident(key + ':assessment'), claim_id=claim, campaign_id=ident(key + ':campaign'), supersedes_id=old_assessment,
            label=label, tested_conditions=dict(checks=len(selected), run_status=status, epochs=EPOCHS if selected else 'Source-availability audit only', data_origin='archived-data'),
            evidence=evidence, justification=reason, limitations=reason + ' No independent/human review; source audits and arithmetic do not validate the full empirical or operational claim.', reviewer=REVIEWER)))
        prior = next(c for c in before['claims'] if c['claim_id'] == claim)['latest_gap']
        records.append(dict(kind='gaps', record=dict(id=ident(key + ':gap'), claim_id=claim, assessment_id=ident(key + ':assessment'), supersedes_id=prior['id'] if prior else None,
            status='open', reason=reason, next_check=next_check, required_inputs=required, dependencies=[], feasibility='Requires separately scoped domain work and exact prerequisites; automatic retry cannot supply missing data or causal identification.',
            estimated_cost_usd=None, cost_basis='Additional workload/storage/equipment/model charges not estimated; unknown is not zero.', decision_rule='Resolve only when a prespecified adequate check covers the claimed scope; numerical subchecks alone do not promote the whole claim.',
            stopping_rule='Stop this bounded attempt at missing prerequisites; retain the excluded experiment and successful scoped checks.', priority=4, campaign_id=ident(key + ':campaign'))))
        queue_requests.append(dict(enqueue=dict(request_id=ident(key + ':job'), campaign_id=ident(key + ':campaign'), execution_mode='manual', priority=4,
            dependencies=[], exclusive_resources=[], raw_input=None, rationale='User-requested unresolved-claim follow-up; externally executed by a domain operator under the retained frozen protocol. ' + reason, followup_of=old_job),
            attach=dict(request_id=ident(key + ':attach'), job_id=ident(key + ':job'), action='attach-run', assessment_ids=[], run_id=run_id,
            reason=('Scientific experiment excluded; this is not a software failure. ' if status == 'excluded' else 'Partial scientific evidence awaiting review. ') + reason)))
    numeric = [('relative-figures', r) for r in result['relative_cells']] + [('rfc7872', r) for r in result['rfc_source_checks']]
    comparison_configs = {}
    for key, row in numeric:
        obs = row['observation_id']
        if key == 'relative-figures':
            figure, ri, _ = obs.split('-')
            config_identity = f'{figure[3:]}:{ri}:{row["left"][-2]}'
            comparison_configs[config_identity] = dict(id=ident('comparison-configuration:' + config_identity), identity=config_identity,
                details=dict(left=row['left'][:-1], right=row['right'][:-1]),
                input_versions=dict(container_sha256=manifest['sha256']), requested_resources=dict(new_network_hosts=0))
        else:
            config_identity = 'paper-section7.4'
        p_id = ident('published:' + obs)
        location = row['location'].replace('Physical PDF page', 'p').replace('Paper physical PDF page', 'p').replace(', Figure', ' Fig').replace(', row', ' r').replace(', column', 'c')
        records.append(dict(kind='published_values', record=dict(id=p_id, source_id=ident('source:paper'), location=location, observation_id=obs,
            configuration_identity=config_identity, metric=row['metric'], units=row['units'], value=row['published'], status='recorded', reason=None,
            extraction='PDF.')))
        # Per-cell outcomes and criterion remain in the original output and frozen
        # protocol. Avoid repeating the same boilerplate in every database row.
        details = {}
        if key == 'relative-figures':
            details.update(valid_days=row['valid_days'])
        else:
            details.update(numerator=row.get('numerator'), denominator=row.get('denominator'), scope='Source arithmetic/quotation only')
        records.append(dict(kind='measurements', record=dict(id=ident('measurement:' + obs), run_id=ident(key + ':run'), published_id=p_id, observation_id=obs,
            configuration_identity=config_identity, metric=row['metric'], units=row['units'], value=row['value'], status='recorded' if row['value'] is not None else 'missing',
            reason=None if row['value'] is not None else 'No complete mean operands', details=details)))
    for row in result['route_sentinels']:
        for group in ['total', *row['recorded_classes']]:
            obs = row['date'] + '-' + row['measurement_id'] + '-' + group
            value = row['class_sum'] - row['total'] if group == 'total' else row['path_class_sums'][group] - row['recorded_classes'][group]
            records.append(dict(kind='measurements', record=dict(id=ident('measurement:route:' + obs), run_id=ident('route-validation:run'), published_id=None, observation_id=obs,
                configuration_identity=row['date'] + '-' + row['measurement_id'], metric='route_count_conservation' if group == 'total' else 'path_classification_consistency', units='traces', value=value,
                status='recorded', reason=None, details=dict(group=group))))
    records.extend(dict(kind='configurations', record=c) for c in comparison_configs.values())
    for entry in records:
        workflow.validate_record(entry['kind'], entry['record'])
    artifacts = []
    for name, media, transform in [('inputs.tar.gz', 'application/gzip', 'Lossless container of339 original source files; original per-member hashes in manifest'),
        ('input-manifest.json', 'application/json', 'Parsed metadata describing all original input identities'), ('registration.json', 'application/json', 'Original frozen local registration; unchanged'),
        ('results.json', 'application/json', 'New deterministic results; published targets and measured arithmetic are distinct fields'),
        ('analysis-main-v1.py', 'text/x-python', 'Exact implementation bytes used for the main execution'), ('production-before.json', 'application/json', 'Read-only target and claim/queue status snapshot; owner credentials excluded'),
        ('import.json', 'application/json', 'Retained first serialization candidate; interactive byte budget exceeded, never stored as scientific rows'),
        ('import-v2.json', 'application/json', 'Retained second serialization candidate; interactive byte budget exceeded, never stored as scientific rows'),
        ('import-v3.json', 'application/json', 'Retained third serialization candidate; conservative interactive byte budget exceeded after import provenance, never stored as scientific rows')]:
        artifacts.append(dict(path=name, sha256=sha((OUT / name).read_bytes()), media_type=media, version='20261007-followup-v1', transformation=transform))
    bundle = dict(version=1, records=records, artifacts=artifacts,
        provenance=dict(producer='Codex primary assistant; AI implementer/evaluator, no human review', execution_revision=result['execution_revision'], execution_dates=result['started_at'] + ' through ' + result['ended_at'],
            limitations='Source availability audits are not scientific reproduction. Same original sources and shared execution across claims. Original registration retained; only claim-check database serialization changes applicability from unsupported partial to applicable with explicit partial scope. No frozen scientific protocol, measurement or prior history was changed.'))
    save(OUT / 'import-v4.json', bundle)
    save(OUT / 'queue-requests-v4.json', queue_requests)
    print(json.dumps(dict(records=len(records), bytes=len(workflow.canonical(bundle)), projected_snapshot_conservative_bytes=before['snapshot_bytes'] + sum(len(workflow.canonical(e['record'])) + 120 for e in records), record_counts=dict(Counter(e['kind'] for e in records))), indent=2))

def verify_local():
    """Repeat the retained implementation; explicitly a regression, not independent validation."""
    import importlib.util
    from decimal import Decimal, localcontext
    original = json.loads((OUT / 'results.json').read_bytes())
    source = OUT / 'analysis-main-v1.py'
    if sha(source.read_bytes()) != original['execution_revision']:
        raise ValueError('Retained execution revision changed')
    directory = OUT / 'verification-v1'; directory.mkdir(exist_ok=False)
    for name in ('inputs.tar.gz', 'input-manifest.json', 'registration.json'):
        shutil.copyfile(OUT / name, directory / name)
    spec = importlib.util.spec_from_file_location('retained_followup', source)
    retained = importlib.util.module_from_spec(spec); spec.loader.exec_module(retained)
    retained.ROOT = ROOT; retained.OUT = directory
    retained.run()
    reproduced = json.loads((directory / 'results.json').read_bytes())
    fields = ('relative_cells', 'route_sentinels', 'rfc_source_checks', 'missing_original_inputs', 'count_invariants', 'summary')
    if any(original[field] != reproduced[field] for field in fields):
        raise ValueError('Retained implementation rerun differs')
    # Exact decimal arithmetic supplies a separate check of the float folding.
    # It shares original count inputs, selected observations and axis mapping.
    _, data = retained.load_inputs(); grouped, _, _ = retained.daily_folds(data)
    maximum = Decimal(0)
    with localcontext() as context:
        context.prec = 40
        means = {key: sum((Decimal(100) * r['numerator'] / r['denominator'] for r in population.values() if r['status'] == 'recorded'), Decimal(0)) /
                 sum(r['status'] == 'recorded' for r in population.values()) for key, population in grouped.items()}
        for cell in original['relative_cells']:
            exact = means[tuple(cell['left'])] - means[tuple(cell['right'])]
            maximum = max(maximum, abs(exact - Decimal(str(cell['value']))))
            if abs(exact - Decimal(str(cell['published']))) > Decimal('0.0051'):
                raise ValueError('Decimal criterion disagrees with float check')
    if maximum > Decimal('1e-10'):
        raise ValueError('Float arithmetic differs materially from Decimal control')
    assert retained.rate(0, 10, 'TIMEOUT')['value'] == 0
    assert retained.rate(None, 10, 'TIMEOUT')['status'] == 'missing'
    assert retained.rate(0, 0, 'TIMEOUT')['status'] == 'invalid'
    assert retained.rate(15, 10, 'TIMEOUT')['status'] == 'invalid'
    assert retained.rate(15, 10, 'tcp_fallback')['value'] == 150
    import importer
    metadata, _, _ = importer.prepare(OUT / 'import-v4.json', '4aaf0cfe-4d48-4d52-a66e-7f61b432a918')
    report = dict(version=1, verified_at=stamp(), passed=True,
        retained_execution_sha256=original['execution_revision'], rerun_fields_equal=list(fields),
        decimal_control_cells=1536, decimal_vs_float_max_difference_pp=str(maximum),
        controls='Recorded zero, missing numerator, undefined zero denominator, bounded proportion, unbounded TCP/UDP ratio.',
        importer_records=len(metadata['records']), importer_artifacts=len(metadata['artifacts']),
        independence='Retained-implementation rerun is a regression. Decimal control shares the same original counts, observation selection and PDF-axis mapping; neither checks raw packet parsing or supplies new empirical data.')
    save(OUT / 'local-verification.json', report)
    print(json.dumps(report, indent=2))

def report():
    result = json.loads((OUT / 'results.json').read_bytes())
    production = json.loads((OUT / 'production-verification.json').read_bytes())
    manifest, data = load_inputs()
    retrieval = json.loads(data['evidence/archive-retrieval-v2.json'])
    archived = {r['path']: r for r in retrieval['records'] if r.get('path')}
    inventory = []
    for member in manifest['members']:
        path = member['path']; previous = archived.get(path)
        source_url = previous.get('url') if previous else None
        if path == 'raw/main.pdf':
            source_url = 'https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf'
        elif path == 'raw/literature/37-0.bin':
            source_url = 'https://www.rfc-editor.org/rfc/rfc7872.txt'
        inventory.append(dict(**member, source_url=source_url, access_status='retained-original-bytes',
            review_status='partial-review', retrieval_record=previous,
            examined='Declared configurations, metrics, input identities or relevant source sections only; see frozen protocols and original reports.',
            unexamined='Full content outside the stated checks; retrieval and hash verification do not establish complete review.'))
    save(OUT / 'source-inventory.json', dict(version=1, created_at=stamp(), members=inventory,
        additional_retrieval_reports=['pilot-inventory.json', 'pilot-inventory-attempt2.json', 'pilot-inventory-attempt3.json', 'pilot-detail-recovery.json', 'pilot-samples.json'],
        review_definition='Complete review requires substantive examination of all sections, tables, figures, appendices and supplements. No new complete review is claimed.', reviewer=REVIEWER))
    paired = [r for r in result['relative_cells'] if r['paired_mean_pp'] is not None]
    changed = [r for r in paired if abs(r['paired_sensitivity_difference_pp']) > 1e-9]
    discrepant = [r for r in paired if abs(r['paired_mean_pp'] - r['published']) > 0.0051]
    grouped, _, _ = daily_folds(data)
    mechanism = []
    for cell in discrepant:
        missing = [{side: cell[side], 'nonrecorded_days': {day: r for day, r in grouped[tuple(cell[side])].items() if r['status'] != 'recorded'}} for side in ('left', 'right')]
        mechanism.append(dict(cell=cell, original_input_exclusions=missing,
            demonstrated_mechanism='Same original inputs/configurations: subtracting independently available-day means versus averaging exact shared-day contrasts changes this value. This establishes an aggregation/coverage sensitivity, not a causal network effect or a defect in the published generating revision.'))
    sensitivity = dict(version=1, analysis='Predeclared matched-day sensitivity of original main output', paired_cells=len(paired), changed_cells=len(changed),
        maximum_change_pp=max(abs(r['paired_sensitivity_difference_pp']) for r in paired), alternative_criterion_disagreements=len(discrepant), mechanism_checks=mechanism,
        cross_epoch_pairing_exclusions=384, interval_kind='none', limitation='Deterministic descriptive sensitivity sharing original inputs and selection; no independent replication or inferential interval.')
    save(OUT / 'paired-sensitivity-check.json', sensitivity)
    with (OUT / 'comparisons.csv').open('x', newline='') as stream:
        fields = ['observation_id', 'location', 'metric', 'units', 'left_configuration', 'right_configuration', 'published', 'value', 'delta_pp', 'assessment', 'valid_days', 'paired_days', 'paired_mean_pp', 'paired_sensitivity_difference_pp']
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for row in result['relative_cells'] + result['rfc_source_checks']:
            item = {k: row.get(k) for k in fields}
            item['left_configuration'] = json.dumps(row['left']) if row.get('left') else None
            item['right_configuration'] = json.dumps(row['right']) if row.get('right') else None
            item['valid_days'] = json.dumps(row['valid_days']) if row.get('valid_days') else None
            writer.writerow(item)
    records = []
    retrieval_rows = json.loads((OUT / 'pilot-samples.json').read_bytes())['records']
    for day in ('2025-05-25', '2025-06-25'):
        raw = data[f'followup/pilot-samples/atlas_results_{day}.json']
        attempt = next(r for r in retrieval_rows if day in r['url'])
        records.append(dict(kind='sources', record=dict(id=ident('source:atlas:' + day),
            citation='Author archive: RIPE Atlas mapped path and class summary for ' + day,
            source_url=attempt['url'], retrieved_url=attempt['retrieved_url'], kind='dataset', role='Archived route-summary data for a limited structural consistency check',
            retrieved_version='Original response bytes retrieved ' + attempt['ended_at'] + '; filename epoch ' + day + ', not a new measurement',
            sha256=sha(raw), byte_count=len(raw), access_status='retrieved', review_status='partial-review',
            review_definition='Complete review requires substantive examination of the entire resource and its provenance; this follow-up is partial.',
            examined='All four measurement class counts, every mapped-path multiplicity and total-count conservation; AS6830 token presence and unknown-hop counts structurally inspected.',
            unexamined='Independent validation of every mapped hop, original raw Atlas/probe results, ASN/geo mapping versions, every DNS-query route and full campaign-day coverage.',
            retrieval_attempts=[attempt], reviewer=REVIEWER,
            findings='Four measurement summaries reconcile class and mapped-path multiplicity totals on this date. This does not verify original mapping correctness or representativeness.',
            limitations='Repeated, author-mapped path counts are not independent trials. No raw-packet or mapping replay, human or independent review.')))
    records.append(dict(kind='summaries', record=dict(id=ident('relative-figures:paired-sensitivity-v1'), run_id=ident('relative-figures:run'),
        metric='paired-day-sensitivity', units='percentage points', statistics=dict(paired_cells=len(paired), changed_cells=len(changed), max_change_pp=sensitivity['maximum_change_pp'], alternative_criterion_disagreements=len(discrepant),
            configuration_dimensions=['epoch', 'family', 'cohort', 'upstream', 'mtu', 'edns_bytes'], discrepant_observation_ids=[r['observation_id'] for r in discrepant],
            controlled_mechanism='Missing May30,2025 DNSSEC/LGI/IPv6/MTU1500/EDNS1232 packet counts give20 versus21 available days. Shared-day comparison uses20 days on both sides.'),
        interval_kind='none', uncertainty_method=sensitivity['limitation'], planned=1536, unexpected=0, recorded=len(paired), missing=0, invalid=0, ambiguous=0, excluded=384)))
    for entry in records:
        workflow_modules().validate_record(entry['kind'], entry['record'])
    save(OUT / 'evidence-records.json', records)
    text = f'''# Nine unresolved IPv6 claim follow-ups — 2026-10-07

The nine requested follow-ups were recorded in study `{STUDY}`. Five runs contain partial evidence; four substantive experiments were excluded with exact prerequisites and reasons. They are archived-data checks and source audits, with no new network measurements. All20 previous queue jobs and all original assessments remain retained. No study publication or application deployment was performed.

Study workspace: https://jumpserve.quaint-lab.org/module/research-verification/workspace/{STUDY}

Reviewed paper: [PuRe item3670144_1/file3670145](https://pure.mpg.de/pubman/item/item_3670144_1/component/file_3670145/main.pdf),22 pages, DOI10.1145/3730567.3764439. Original PDF SHA256: `f6378fb3de19aa8d3204f4d3a2028f95497f334862a5298aa6678ef1b816290b`.

## Findings and coverage

All1536 difference cells in Figures13–15c/d and16a/b match under the frozen primary criterion of0.0051 percentage points. The largest absolute difference is{result['summary']['max_absolute_relative_difference_pp']:.12f}pp. This reproduces arithmetic under published aggregation, not every time-series point or upstream packet parser.

The matched-day sensitivity changes18 cells. One cell, Figure13d row13,column4 (`fig13d-12-3`), crosses the printing-agreement criterion: published−0.01pp; difference of available-day means−0.005907273279pp; matched-day mean+0.001349018415pp. May30,2025 packet counts are absent for the DNSSEC/LGI/IPv6/MTU1500/EDNS1232 configuration. The primary comparison uses20 versus21 available days, while the sensitivity uses the exact20-day intersection. This demonstrates an aggregation/coverage mechanism on the retained data; it does not establish a network causal effect or which released revision generated the paper.

Eight route summaries on two preselected sentinel dates contain39049 recorded traceroute counts. Their four class totals and all mapped-path multiplicities reconcile. These repeated counts are not39049 independent replications. No full-day census, raw Atlas/probe replay, independent IP-to-AS mapping or route-to-every-DNS-query join was completed.

Six fragment-loss quotations match [RFC7872 Tables1–2](https://www.rfc-editor.org/rfc/rfc7872.txt). Of four percentages recomputed from the paper’s published integer counts, one exceeds the printing criterion:105/315×100=33.333333…%, rounding33.33%, versus33.34% in physical PDF page13,Section7.4. This is a small source arithmetic discrepancy; it does not resolve the unmatched historical TCP/ICMP comparison.

| Claim | Run | Current label | Remaining requirement |
|---|---|---|---|
| Fragment-drop causal effect | Partial | Inconclusive | Route-equal policy intervention and matched packet/query/population identities |
| DNSSEC/IPv6 association | Partial | Inconclusive | Independently assigned cohorts, NS-set/provider identities and weighting |
| Global DNS readiness | Excluded | Inconclusive | Parse historical domain/NS-set mappings and sampling weights under a larger protocol |
| Historical RFC comparison | Partial | Inconclusive | Matched historical targets, transports, epochs and raw traces |
| IPv6 operational recommendation | Excluded | Inconclusive | Multi-operator/resolver operational and security outcomes |
| APNIC global fragment loss | Excluded | Untested | Original APNIC ad observations, weights, targets and policies |
| Packet-size mean/SD | Excluded | Untested | Exact May1 PCAPs, parser revision and capture/length controls |
| RIPE Atlas route coverage | Partial | Inconclusive | Full raw probe/trace coverage, mapping versions and query joins |
| Every relative cell/plotted observation | Partial | Inconclusive | Remaining plotted observations in Figures3–12 and17–21 |

Current claim totals:3 reproduced,3 discrepant,7 inconclusive,2 untested. Two previously untested claims now have relevant partial checks and become inconclusive; no broad claim became fully resolved. Reproduced means the declared criterion passed under its recorded conditions; discrepant means examined evidence conflicts with a stated value or criterion; inconclusive means relevant checks cannot resolve the whole scope; untested means no adequate empirical check was completed. An availability audit does not change an untested claim into a reproduced result.

The four excluded runs appear as “Failed” jobs because the current queue maps excluded runs to that status. Their reasons explicitly say they are scientific exclusions, not software test failures. Five partial runs await review. All original jobs remain unchanged, including the earlier manual planning tasks; the nine follow-ups link to them through `followup_of`.

## Reproduction and provenance

Nine version1 protocols were frozen locally before main execution and saved unchanged in `registration.json`. Inputs:339 original files,48470849 original bytes,8243085 compressed container bytes. Container SHA256: `{manifest['sha256']}`. Main execution:{result['started_at']} through{result['ended_at']}; measured shared wall time{result['wall_seconds']:.6f}s. Exact implementation SHA256: `{result['execution_revision']}` in `analysis-main-v1.py`. The nine scoped records share this execution and inputs; they are not independent replications.

The initial network-sandbox failures, Python default-CA failure, and directory-report save failure are preserved separately from main results. System-CA verification enabled subsequent retrieval; no TLS verification was disabled. Seven recovered directory originals retain hashes, while lost retrieval timestamps/response metadata remain null. Three unused import serializations remain retained; later serialization removes repeated boilerplate and normalizes256 full comparison configurations to fit the existing4MB interactive export bound. No frozen protocol or original numerical result changed.

From the backend repository, using Python3.13 and `pypdf==6.19.0`:

```sh
experiments/http2_compliance/.venv/bin/python -B experiments/ipv6_dns/followup.py verify-local
```

This re-executes the retained exact implementation in a fresh append-only verification directory and compares all numerical outputs, identities and exclusions. If that directory already exists, use a fresh copy of this study directory; preserve earlier verification results. A fresh campaign follows `prepare-inputs`, `freeze`, then `run` in a fresh workspace with all retained original inputs. Saved registrations/results reject overwriting.

The40-digit Decimal control agrees with all1536 float contrasts within7.84×10⁻¹⁵pp. Recorded zero, missing numerator, zero denominator, bounded percentages and the potentially unbounded TCP/UDP ratio also pass their controls. These controls share original counts and axis mapping and do not validate upstream packet processing. Dates, paths, servers and changing NS sets overlap; no independent sample or inferential confidence interval is justified. Min/max and sensitivities are descriptive.

`comparisons.csv` exports1546 primary numerical/source checks with separate published and computed fields, full comparison identities, valid-day counts and paired sensitivities. Empty CSV fields are missing/inapplicable, not recorded zero. JSON retains nulls and reasons. `source-inventory.json`, `input-manifest.json`, the original retrieval reports and the two appended route-source records identify versions, hashes, access/review scope and limitations. Complete review requires substantive examination of an entire source including all figures, tables, appendices and supplements; new source reviews are partial. No human or independent review is claimed.

## Persistence and verification

Verified targets: AWS395567831870 and Supabase`regphejnlvfpyokpniny`.3462 scientific records were appended:9 frozen protocols/campaigns/checks/runs/assessments/gaps,265 configurations,1546 separate published values,1586 measurements,9 summaries and2 source-review records. Ten initial private artifacts total19558819 stored payload bytes. Every stored field and original artifact hash was checked after retrieval, including all339 expanded original members. Supplementary reports, a paired-day summary and two route-source records are saved separately with their own storage receipt.

The retained-implementation/Decimal scientific controls and server-side record/hash checks pass. A legitimate authenticated browser request was not exercised in this turn; the study was updated by an authorized trusted operator using existing ownership, not a fabricated Google session. No frontend/backend application code or schema was deployed. Earlier release checks do not constitute a new browser check. Scientific exclusions and the source arithmetic discrepancy are findings, not failing software tests.

No experiment hosts or model-evaluation calls were requested. Local CPU/memory, database/storage/transfer, hosting and Codex usage costs are unallocated; measured and estimated charges remain missing, not zero. Only logical stored payload bytes and execution wall times are reported; charges, database/index overhead and peak memory were not reconciled.
'''
    for old, new in {
        'All20': 'All 20', 'All1536': 'All 1,536', 'all1536': 'all 1,536',
        'DOI10.': 'DOI 10.', 'Figures13': 'Figures 13', 'Figures3': 'Figures 3',
        'and16a/b': 'and 16a/b', 'of0.0051': 'of 0.0051',
        'changes18': 'changes 18', 'Figure13d row13,column4': 'Figure 13d row 13, column 4',
        'May30,2025': 'May 30, 2025', 'uses20 versus21': 'uses 20 versus 21',
        'exact20-day': 'exact 20-day', 'contain39049': 'contain 39,049',
        'not39049': 'not 39,049', 'is{': 'is {', 'page13,Section7.4': 'page 13, Section 7.4',
        'Tables1–2': 'Tables 1–2', '3354': '3354',
        '3 reproduced,3 discrepant,7 inconclusive,2 untested': '3 reproduced, 3 discrepant, 7 inconclusive, 2 untested',
        'version1': 'version 1', 'Inputs:339': 'Inputs: 339', ',48470849': ', 48,470,849',
        ',8243085': ', 8,243,085', 'Execution:': 'Execution: ',
        'normalizes256': 'normalizes 256', 'existing4MB': 'existing 4 MB',
        'Python3.13': 'Python 3.13', 'The40-digit': 'The 40-digit',
        'within7.84': 'within 7.84', 'exports1546': 'exports 1,546',
        'AWS395567831870': 'AWS 395567831870', 'Supabase`': 'Supabase `',
        '.3462': '. 3,462', ':9 frozen': ': 9 frozen', ',265': ', 265',
        ',1546': ', 1,546', ',1586': ', 1,586', ',9 summaries': ', 9 summaries',
        'and2 source': 'and 2 source', 'total19558819': 'total 19,558,819',
        'all339': 'all 339', 'data for a': 'data for a',
    }.items():
        text = text.replace(old, new)
    write(OUT / 'report-v2.md', text.encode())
    print(json.dumps(dict(report=str(OUT / 'report.md'), comparisons=1546, paired_sensitivity_disagreements=len(discrepant), supplemental_records=len(records))))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inventory', 'inventory-detail', 'recover-detail', 'retrieve-samples', 'prepare-inputs', 'freeze', 'run', 'build-import', 'verify-local', 'report'])
    parser.add_argument('--attempt', type=int, choices=[1, 2, 3], default=1)
    args = parser.parse_args()
    if args.command == 'inventory':
        inventory(args.attempt)
    elif args.command == 'inventory-detail':
        inventory(detail=True)
    elif args.command == 'recover-detail':
        recover_detail()
    elif args.command == 'retrieve-samples':
        retrieve_samples()
    elif args.command == 'prepare-inputs':
        prepare_inputs()
    elif args.command == 'freeze':
        freeze()
    elif args.command == 'build-import':
        build_import()
    elif args.command == 'verify-local':
        verify_local()
    elif args.command == 'report':
        report()
    elif args.command == 'run':
        try:
            run()
        except Exception as error:
            save(OUT / 'main-failed-attempt.json', dict(status='failed', ended_at=stamp(), reason=str(error),
                execution_revision=sha(Path(__file__).read_bytes()), protocol_registration_sha256=sha((OUT / 'registration.json').read_bytes()),
                limitation='Failed execution preserved; no successful run or scientific conclusion inferred.'))
            raise

if __name__ == '__main__':
    main()
