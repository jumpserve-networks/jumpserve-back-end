"""Bounded, append-only follow-ups for the nine unresolved IPv6 claims.

Inventory is an exploratory retrieval pilot, not an experiment or validation.
No arbitrary URLs or code execution, credentials, infrastructure or network probes.
"""
import argparse
import datetime as dt
import hashlib
import gzip
import io
import json
import math
from pathlib import Path
import platform
import re
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

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inventory', 'inventory-detail', 'recover-detail', 'retrieve-samples', 'prepare-inputs', 'freeze', 'run'])
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
