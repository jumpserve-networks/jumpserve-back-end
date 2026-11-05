"""Incident-specific, read-only evidence projection; never a reconciliation adapter."""
import hashlib
import re
import uuid

from .common import Hold

CAPABILITY = {
    'version': 'platform-capability-pack-v1',
    'id': 'assessment-native-failure-evidence',
    'revision': 1,
    'description': 'Bounded read-only diagnostics for the retained native failure in assessment fe761052-6e57-4d47-a3de-163c6cad905b.',
    'instructions': 'The verified supervisor invokes collect(context) only after frozen self-update qualification and exact installed-source verification. Read references/capabilities/diagnostics-assessment-native-failure-evidence.json. Missing indexed originals require a scoped trusted handoff refresh, never replay or reconciliation. Historical receipts and cleanup claims are not current qualification or provider non-execution proof.',
    'files': {
        'README.md': '# Native failure evidence\n\nThis pack supplies a controller-invoked read-only collector, not a cloud or reconciliation tool. The controller retains originals privately and delivers a bounded redacted projection. No sandbox command fetches private originals.\n\nRead references/capabilities/diagnostics-assessment-native-failure-evidence.json after verified installation. missing-prerequisite means the trusted producer must supply the exact indexed originals and complete ledger or current fingerprints. HTTP 503 never proves non-execution. Installation is not assessment resumption or scientific completion.\n'
    }
}

ASSESSMENT = 'fe761052-6e57-4d47-a3de-163c6cad905b'
TASK = '903c2cc6-5309-5fc3-9bf4-7d7007e02a09'
INCIDENT = 'e2908a8f-78b4-434d-aa30-3d1abfda2491'
DISPATCH = '1cdebd72-29a7-5249-af00-c4643c97c446'
CALL = '6ad42538-bdf7-4451-a4d5-61bdc7062ad6'
REQUIRED = {
    'request': ('188d0474-9bf5-5ff0-8c53-d0cc28f63c9b', '1c62acb564fbfc93047b0a2742587855eed7df729af56fc420d91ff0c348fced'),
    'response': ('527d0536-3ebc-5162-96a4-c03beeadc872', '26f8dd29ba2931fa849e1f2fe7d0e63f9aeb03f1286d9e10642cfb70e0ac1ce0')
}
MAX_ROWS = 4096
MAX_BYTES = 1048576
MAX_BRIDGE = 32


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _count(value):
    return value if type(value) is int and 0 <= value <= 1000000000 else None


def _rows(packet, key):
    rows = packet.get(key)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or any(not isinstance(row, dict) for row in rows):
        return None
    return rows


def _index(packet):
    """Mirror only the accessor's public index, never nested artifact hints."""
    index = {}
    for key in ('native_calls', 'dispatches', 'artifacts'):
        for row in _rows(packet, key) or []:
            for id_key, sha_key in (('request_artifact_id', 'request_sha256'),
                                    ('response_artifact_id', 'response_sha256'), ('id', 'sha256')):
                ident, sha = row.get(id_key), row.get(sha_key)
                if _uuid(ident) and sha is not None:
                    index.setdefault(ident, set()).add(sha if _sha(sha) else None)
    return index


def _original(context, index, ident, sha):
    result = {'artifact_id': ident, 'sha256': sha}
    hashes = index.get(ident, set())
    if not hashes:
        return result | {'status': 'missing-indexed-original'}
    if len(hashes) != 1:
        return result | {'status': 'ambiguous-original-identity'}
    if sha not in hashes:
        return result | {'status': 'indexed-hash-mismatch'}
    # Hold/transport errors deliberately propagate to the frozen controller.
    # Do not swallow uncertainty or turn it into a successful empty diagnosis.
    try:
        raw = context.original(ident)
    except Hold:
        raise
    except ValueError:
        return result | {'status': 'original-unavailable-or-outside-accessor-bound'}
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        return result | {'status': 'invalid-or-oversize-original'}
    if hashlib.sha256(raw).hexdigest() != sha:
        return result | {'status': 'original-hash-mismatch'}
    return result | {'status': 'verified-original', 'byte_count': len(raw)}


def _fingerprints(packet):
    """Expose only typed fingerprints; receipt claims never certify installation.

    Optional producer schema: installed_fingerprints.{runtime,database} contains
    source_sha256, qualification_sha256, receipt_sha256, assessment_id, task_id.
    No matching producer field means unavailable, not inferred from manifests.
    """
    source = packet.get('installed_fingerprints')
    source = source if isinstance(source, dict) else {}
    result = {}
    for key in ('runtime', 'database'):
        row = source.get(key)
        if (isinstance(row, dict) and row.get('assessment_id') == ASSESSMENT
                and row.get('task_id') == TASK
                and all(_sha(row.get(k)) for k in ('source_sha256', 'qualification_sha256', 'receipt_sha256'))):
            result[key] = {k: row[k] for k in ('source_sha256', 'qualification_sha256', 'receipt_sha256')}
            result[key]['status'] = 'task-bound-producer-fingerprints-not-independently-verified'
        else:
            result[key] = {'status': 'unavailable'}
    if _sha(packet.get('recovery_contract_sha256')):
        result['database']['observed_recovery_contract_sha256'] = packet['recovery_contract_sha256']
        result['database']['recovery_contract_is_qualification_receipt'] = False
    return result


def collect(context):
    """Read only context.packet and its indexed hash-checked original accessor."""
    packet = context.packet
    out = dict(version='assessment-native-failure-evidence-v1', status='missing-prerequisite',
               assessment_id=ASSESSMENT, assessment_task_id=TASK, incident_id=INCIDENT,
               dispatch_id=DISPATCH, native_call_id=CALL, dispatch_outcome='unconfirmed',
               safe_to_replay=False, reconciled=False, scientific_completion=False,
               missing_prerequisites=[])
    missing = out['missing_prerequisites']
    if (not isinstance(packet, dict) or packet.get('assessment_id') != ASSESSMENT
            or packet.get('assessment_task_id') != TASK
            or not isinstance(packet.get('blocker'), dict) or packet['blocker'].get('id') != INCIDENT
            or (isinstance(packet.get('task'), dict) and packet['task'].get('id') != TASK)):
        missing.append('task-assessment-incident-identity-mismatch')
        return out
    ledgers = {k: _rows(packet, k) for k in ('native_calls', 'dispatches', 'artifacts')}
    if any(v is None for v in ledgers.values()):
        missing.append('missing-malformed-or-oversize-ledger')
        return out
    calls, dispatches = ledgers['native_calls'], ledgers['dispatches']
    selected = [row for row in calls if row.get('id') == CALL]
    parents = [row for row in dispatches if row.get('id') == DISPATCH]
    if (len(selected) != 1 or len(parents) != 1 or selected[0].get('dispatch_id') != DISPATCH
            or any(row.get(k) not in (None, expected) for row in selected + parents
                   for k, expected in (('study_id', ASSESSMENT), ('assessment_id', ASSESSMENT), ('task_id', TASK)))):
        missing.append('missing-or-ambiguous-dispatch-call-identity')
        return out
    call, parent = selected[0], parents[0]
    scoped = [row for row in calls if row.get('dispatch_id') == DISPATCH]
    if any(row.get(k) not in (None, expected_identity) for row in scoped
           for k, expected_identity in (('study_id', ASSESSMENT), ('assessment_id', ASSESSMENT), ('task_id', TASK))):
        missing.append('conflicting-scoped-call-identities')
        return out
    index = _index(packet)
    out['originals'] = {}
    for kind, (ident, sha) in REQUIRED.items():
        if call.get(kind + '_artifact_id') != ident or call.get(kind + '_sha256') != sha:
            item = dict(artifact_id=ident, sha256=sha, status='native-original-pointer-mismatch')
        else:
            item = _original(context, index, ident, sha)
        out['originals'][kind] = item
        if item['status'] != 'verified-original':
            missing.append(kind + ':' + item['status'])
    numbers = [row.get('number') for row in scoped]
    ids = [row.get('id') for row in scoped]
    valid_order = (all(type(n) is int and 1 <= n <= 1000000000 for n in numbers)
                   and len(set(numbers)) == len(numbers) and all(_uuid(i) for i in ids)
                   and len(set(ids)) == len(ids))
    ordered = sorted(scoped, key=lambda row: row['number']) if valid_order else []
    provenance = parent.get('provenance')
    provenance = provenance if isinstance(provenance, dict) else {}
    expected = _count(provenance.get('provider_calls'))
    coverage = packet.get('coverage')
    coverage = coverage if isinstance(coverage, dict) else {}
    projection = packet.get('projection')
    projection = projection if isinstance(projection, dict) else {}
    total = _count(coverage.get('native_calls_total'))
    truncated = projection.get('truncated', 0) != 0
    complete = (valid_order and expected is not None and expected == len(scoped)
                and total == len(calls) and not truncated)
    out['call_coverage'] = dict(status='complete-retained-ledger' if complete else 'incomplete',
        ordered_native_numbers=[row['number'] for row in ordered[:256]],
        observed_dispatch_calls=len(scoped), declared_provider_calls=expected,
        retained_native_total=len(calls), declared_native_total=total,
        native_unknown_total=_count(coverage.get('unresolved_native_total')),
        native_unknown_observed=sum(row.get('status') == 'unknown' for row in calls),
        dispatch_unconfirmed=True, output_truncated=len(ordered) > 256)
    if not complete or len(ordered) > 256:
        missing.append('incomplete-ordered-call-coverage')
    # Native status/HTTP metadata is a recorded observation, not execution proof.
    out['native_observation'] = dict(
        status=call.get('status') if call.get('status') in ('failed', 'unknown', 'complete', 'running', 'queued') else 'unavailable',
        http_status=call.get('http_status') if type(call.get('http_status')) is int and 100 <= call['http_status'] <= 599 else None,
        subscription_unavailable_label=call.get('reason') == 'subscription_sharing_user_unavailable')
    bridge = dict(scope='historical-dispatch-claims-not-current-qualification', artifacts=[])
    for key in ('provider_request_sent', 'response_received', 'native_tool_cleanup'):
        bridge[key] = provenance.get(key) if type(provenance.get(key)) is bool else None
    bridge['container_cleanup'] = provenance.get('container_cleanup') if provenance.get('container_cleanup') in ('stopped', 'running', 'unknown') else 'unavailable'
    refs = provenance.get('artifacts')
    refs = refs if isinstance(refs, list) else []
    retained = []
    for row in refs[:MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        name = row.get('name')
        if name in ('receipt.json', 'completion.json', 'admission.json', 'cleanup.json') or (isinstance(name, str) and re.fullmatch(r'call-[1-9][0-9]{0,3}-completion\.json', name)):
            retained.append(row)
    for row in retained[:MAX_BRIDGE]:
        ident, sha = row.get('id'), row.get('sha256')
        if _uuid(ident) and _sha(sha):
            item = _original(context, index, ident, sha)
            bridge['artifacts'].append(dict(name=row['name'], **item))
            if item['status'] != 'verified-original':
                missing.append('bridge:' + item['status'])
        else:
            missing.append('bridge:invalid-original-pointer')
    bridge['output_truncated'] = len(retained) > MAX_BRIDGE or len(refs) > MAX_ROWS
    if not retained or bridge['output_truncated']:
        missing.append('missing-or-truncated-bridge-originals')
    out['bridge'] = bridge
    out['installed_fingerprints'] = _fingerprints(packet)
    if any(v['status'] == 'unavailable' for v in out['installed_fingerprints'].values()):
        missing.append('current-runtime-or-database-fingerprints-unavailable')
    out['missing_prerequisites'] = sorted(set(missing))
    out['status'] = 'missing-prerequisite' if missing else 'diagnostics-collected'
    out['next_step'] = 'Trusted handoff producer supplies scoped indexed originals, complete coverage and current qualification receipts; no replay or reconciliation.'
    return out
