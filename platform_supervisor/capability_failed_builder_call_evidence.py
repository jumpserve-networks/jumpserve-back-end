"""Task-bound evidence acquisition only; never failed-call admission or settlement."""
import gzip
import io
import json
import re
import stat
import zipfile
import zlib
from pathlib import PurePosixPath
from .common import Hold
from .capability_retained_provider_outcome_diagnostics import sha, strict_json, stream_facts

CAPABILITY = {'version': 'platform-capability-pack-v1', 'id': 'failed-builder-call-evidence', 'revision': 1, 'description': 'Read-only task-bound retained failed builder call evidence and capture-order diagnostics.', 'instructions': 'Only the trusted controller invokes collect after frozen qualification and exact installed module verification. Read the bounded private diagnostics; missing evidence remains missing. This collector cannot reconcile, retry, replay, release, resume, or prove non-execution or scientific completion. The separate retained-provider-outcome-diagnostics scope is unchanged.', 'files': {'README.md': 'Installed host collector only; no sandbox provider or database client. Reads context.packet and context.original only. Ledger call 155 is dispatch-local call 8. Hash-verified original request/response, completion sidecar, parent receipt and checkpoint are inspected when indexed. Unindexed provenance references are reported absent without fallback reads. Reports contain structural facts, not prompts, host paths or credentials. Capture-order comparisons are structural, not clocks or proof of provider non-execution. Failed-transport accounting requires separate installed qualification. Frozen self-update, exact source verification, cancellation and uncertain-call fences remain mandatory.\n'}}

ASSESSMENT = 'fe761052-6e57-4d47-a3de-163c6cad905b'
TASK = '903c2cc6-5309-5fc3-9bf4-7d7007e02a09'
DISPATCH = '1cdebd72-29a7-5249-af00-c4643c97c446'
CALL = '6ad42538-bdf7-4451-a4d5-61bdc7062ad6'
REQUEST_ID = '188d0474-9bf5-5ff0-8c53-d0cc28f63c9b'
REQUEST_SHA = '1c62acb564fbfc93047b0a2742587855eed7df729af56fc420d91ff0c348fced'
RESPONSE_ID = '527d0536-3ebc-5162-96a4-c03beeadc872'
RESPONSE_SHA = '26f8dd29ba2931fa849e1f2fe7d0e63f9aeb03f1286d9e10642cfb70e0ac1ce0'
MAX_BYTES = 1048576
MAX_ROWS = 4096
MAX_OUTPUT = 16000


def binding(value):
    for key, expected in (('study_id', ASSESSMENT), ('assessment_id', ASSESSMENT),
                          ('task_id', TASK), ('assessment_task_id', TASK),
                          ('dispatch_id', DISPATCH), ('call_id', CALL)):
        if key in value and value[key] != expected:
            raise ValueError('identity-conflict')


def facts(value):
    """Allowlist typed transport facts; never copy free text or arbitrary keys."""
    result = {}
    for key in ('response_received', 'provider_request_sent', 'native_tool_cleanup', 'paid_fallback'):
        if type(value.get(key)) is bool: result[key] = value[key]
    for key in ('http_status', 'provider_calls', 'provider_retries'):
        if type(value.get(key)) is int and 0 <= value[key] <= 1000: result[key] = value[key]
    for key, allowed in (('status', ('failed', 'complete', 'unknown', 'invalid-output', 'paused-auth', 'paused-quota')),
                         ('container_cleanup', ('stopped', 'confirmed', 'unknown'))):
        if value.get(key) in allowed: result[key] = value[key]
    return result


def history(raw):
    """Bounded passive ZIP member reads; no extraction and no code execution."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 128 or sum(e.file_size for e in entries) > 4000000:
            raise ValueError('checkpoint-expansion-bound')
        names = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (entry.filename in names or path.is_absolute() or '..' in path.parts or
                '\\' in entry.filename or '\x00' in entry.orig_filename or
                stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                raise ValueError('unsafe-checkpoint')
            names.add(entry.filename)
        def read(name):
            info = archive.getinfo(name)
            if info.file_size > MAX_BYTES or info.is_dir(): raise ValueError('member-bound')
            with archive.open(info) as source: value = source.read(MAX_BYTES + 1)
            if len(value) != info.file_size: raise ValueError('member-size')
            return value
        raw_history = read('native-tool-history.json')
        value = strict_json(raw_history)
        if isinstance(value, dict):
            if (value.get('version') != 'builder-history-gzip-v1' or
                value.get('path') != 'native-tool-history.json.gz' or
                type(value.get('bytes')) is not int or not 0 <= value['bytes'] <= MAX_BYTES or
                type(value.get('records')) is not int or not 0 <= value['records'] <= MAX_ROWS):
                raise ValueError('history-bound')
            compressed = read('native-tool-history.json.gz')
            if sha(compressed) != value.get('compressed_sha256'): raise ValueError('history-hash')
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source: raw_history = source.read(MAX_BYTES + 1)
            if len(raw_history) != value['bytes'] or sha(raw_history) != value.get('sha256'):
                raise ValueError('history-hash')
            expanded = strict_json(raw_history)
            if len(expanded) != value.get('records'): raise ValueError('history-count')
            value = expanded
        if not isinstance(value, list) or len(value) > MAX_ROWS: raise ValueError('history-bound')
        return value


def order_facts(request, checkpoint):
    result = dict(observation='unavailable', expected_capture_sequence='bootstrap,call-sidecar,checkpoint,parent-receipt,parent-completion',
                  temporal_order_verified=False,
                  scope='Structural prefix comparison, not wall-clock or provider execution proof.')
    if request is None or checkpoint is None: return result
    try:
        before = [v for v in request.get('input', []) if isinstance(v, dict) and
                  v.get('type') in ('function_call', 'function_call_output')]
        after = history(checkpoint)
        if not before: return dict(result, observation='request-history-unavailable')
        # Compare exact retained records; missing/changed records cannot be inferred away.
        matches = before == after[:len(before)]
        return dict(result, observation='prefix-consistent' if matches else 'checkpoint-ordering-mismatch',
                    request_records=len(before), checkpoint_records=len(after))
    except (ValueError, KeyError, TypeError, OSError, EOFError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError):
        return dict(result, observation='checkpoint-history-unavailable-or-malformed')


def collect(context):
    report = dict(version='failed-builder-call-evidence-v1', assessment_id=ASSESSMENT,
                  assessment_task_id=TASK, dispatch_id=DISPATCH, call_id=CALL,
                  ledger_call_number=155, dispatch_local_call_number=8,
                  status='inconclusive', reason='identity-unavailable', sources={}, missing=[],
                  reconciled=False, replacement_admitted=False, scientific_completion=False,
                  non_execution_proven=False, assessment_resumed=False,
                  failed_transport_accounting_contract=dict(available=False),
                  bounds=dict(bytes_per_original=MAX_BYTES, originals=6, output_bytes=MAX_OUTPUT))
    try:
        packet = context.packet
        if (packet.get('assessment_id') != ASSESSMENT or packet.get('assessment_task_id') != TASK or
            not packet.get('platform_task_id')): return report
        binding(packet)
        calls = packet.get('native_calls', []); dispatches = packet.get('dispatches', [])
        artifacts = packet.get('artifacts', [])
        if any(not isinstance(v, list) or len(v) > MAX_ROWS or any(not isinstance(r, dict) for r in v)
               for v in (calls, dispatches, artifacts)):
            return dict(report, reason='packet-bound')
        matches = [c for c in calls if c.get('id') == CALL]
        parents = [d for d in dispatches if d.get('id') == DISPATCH]
        if len(matches) != 1 or len(parents) != 1: return dict(report, reason='target-missing-or-ambiguous')
        call = matches[0]; parent = parents[0]; binding(call); binding(parent)
        if call.get('dispatch_id') != DISPATCH or call.get('number') != 155:
            return dict(report, reason='call-identity-conflict')
        for key, expected in (('request_artifact_id', REQUEST_ID), ('request_sha256', REQUEST_SHA),
                              ('response_artifact_id', RESPONSE_ID), ('response_sha256', RESPONSE_SHA)):
            if call.get(key) != expected: return dict(report, reason='call-hash-or-identity-conflict')
        # Mirror only the indices admitted by Evidence.original, never provenance alone.
        indexed = {}
        artifact_ids = [r.get('id') for r in artifacts]
        if len(set(artifact_ids)) != len(artifact_ids): raise ValueError('ambiguous-artifact-index')
        metadata = {r.get('id'): r for r in artifacts}
        for rows in (calls, dispatches, artifacts):
            for row in rows:
                for ik, sk in (('id', 'sha256'), ('request_artifact_id', 'request_sha256'),
                               ('response_artifact_id', 'response_sha256')):
                    ident, digest = row.get(ik), row.get(sk)
                    if not ident or not digest: continue
                    if ident in indexed and indexed[ident] != digest: raise ValueError('ambiguous-index')
                    if not re.fullmatch('[a-f0-9]{64}', digest): raise ValueError('invalid-index-hash')
                    indexed[ident] = digest
        provenance = parent.get('provenance', {})
        refs = provenance.get('artifacts', [])
        if not isinstance(refs, list) or len(refs) > MAX_ROWS: raise ValueError('artifact-bound')
        selected = {}
        for name in ('call-8-completion.json', 'completion.json', 'receipt.json'):
            found = [r for r in refs if r.get('name') == name]
            if len(found) > 1: raise ValueError('ambiguous-artifact')
            selected[name] = found[0] if found else None
        selected['checkpoint'] = provenance.get('checkpoint')
        selected['request'] = dict(id=REQUEST_ID, sha256=REQUEST_SHA)
        selected['response'] = dict(id=RESPONSE_ID, sha256=RESPONSE_SHA)
        retained = {}
        used = set()
        admitted = []
        # Preflight the entire selection before reading any bytes. A small
        # provenance reference must not override conflicting indexed metadata.
        for name, ref in selected.items():
            if not ref:
                report['missing'].append(dict(evidence=name, reason='reference-absent')); continue
            if not isinstance(ref, dict): raise ValueError('invalid-artifact-reference')
            binding(ref)
            ident = ref.get('id', ref.get('artifact_id')); digest = ref.get('sha256')
            if ('id' in ref and 'artifact_id' in ref and ref['id'] != ref['artifact_id'] or
                not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
                raise ValueError('conflicting-artifact-reference')
            if (not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', ident)):
                raise ValueError('invalid-artifact-identity')
            if ident in used: raise ValueError('artifact-alias-conflict')
            used.add(ident)
            if ident not in indexed:
                report['missing'].append(dict(evidence=name, reason='indexed-original-absent')); continue
            if indexed[ident] != digest: raise ValueError('reference-hash-conflict')
            meta = metadata.get(ident, {})
            binding(meta)
            if 'artifact_id' in meta and meta['artifact_id'] != ident:
                raise ValueError('metadata-identity-conflict')
            sizes = [row['byte_count'] for row in (ref, meta) if 'byte_count' in row]
            if any(type(size) is not int or not 0 <= size <= MAX_BYTES for size in sizes):
                raise ValueError('original-byte-bound')
            if len(set(sizes)) > 1: raise ValueError('original-size-conflict')
            size = sizes[0] if sizes else None
            admitted.append((name, ident, digest, size))
        for name, ident, digest, size in admitted:
            try: raw = context.original(ident)
            except Hold: raise
            except (ValueError, KeyError, OSError):
                report['missing'].append(dict(evidence=name, reason='original-unavailable')); continue
            if (not isinstance(raw, bytes) or len(raw) > MAX_BYTES or sha(raw) != digest or
                size is not None and len(raw) != size): raise ValueError('original-hash-or-size-conflict')
            retained[name] = raw
            report['sources'][name] = dict(artifact_id=ident, sha256=digest, bytes=len(raw))
        documents = {}
        for name in ('request', 'call-8-completion.json', 'completion.json', 'receipt.json'):
            if name not in retained: continue
            try:
                value = strict_json(retained[name])
                if not isinstance(value, dict): raise ValueError('object-required')
                binding(value); documents[name] = value
            except (ValueError, TypeError, RecursionError):
                report['missing'].append(dict(evidence=name, reason='malformed-or-conflicting-document'))
        report['ledger_transport'] = facts(call)
        report['parent_transport'] = facts(provenance)
        side = documents.get('call-8-completion.json')
        if side is not None:
            required = dict(call_id=CALL, dispatch_id=DISPATCH, task_id=TASK, study_id=ASSESSMENT,
                            request_sha256=REQUEST_SHA, response_sha256=RESPONSE_SHA,
                            response_filename='call-8-response.bin')
            if (any(side.get(k) != v for k, v in required.items()) or
                side.get('version') != 'builder-native-call-reconciliation-v1' or
                type(side.get('response_received')) is not bool or
                side.get('status') not in ('complete', 'failed', 'unknown', 'invalid-output', 'paused-auth', 'paused-quota') or
                side.get('http_status') is not None and type(side['http_status']) is not int):
                raise ValueError('sidecar-binding-conflict')
            report['completion_transport'] = facts(side)
            report['completion_matches_ledger'] = all(side.get(k) == call.get(k) for k in ('status', 'response_received', 'http_status'))
        receipt = documents.get('receipt.json')
        if receipt is not None:
            report['receipt_transport'] = facts(receipt)
            keys = ('status', 'http_status', 'response_received', 'provider_calls', 'container_cleanup', 'native_tool_cleanup', 'checkpoint')
            report['receipt_matches_parent'] = all(
                k in receipt and k in provenance and type(receipt[k]) is type(provenance[k]) and receipt[k] == provenance[k]
                for k in keys)
            pointer = receipt.get('checkpoint')
            selected_checkpoint = report['sources'].get('checkpoint')
            report['receipt_checkpoint_matches_selected_original'] = (
                isinstance(pointer, dict) and selected_checkpoint is not None and
                pointer.get('artifact_id', pointer.get('id')) == selected_checkpoint['artifact_id'] and
                pointer.get('sha256') == selected_checkpoint['sha256'] and
                not ('id' in pointer and 'artifact_id' in pointer and pointer['id'] != pointer['artifact_id']))
        completion = documents.get('completion.json')
        if completion is not None:
            nested = completion.get('receipt')
            if not isinstance(nested, dict):
                report['missing'].append(dict(evidence='completion.json', reason='parent-receipt-absent'))
            else:
                binding(nested)
                report['parent_completion_transport'] = facts(nested)
                report['parent_completion_matches_receipt'] = receipt is not None and nested == receipt
                report['parent_completion_hash_matches_provenance'] = sha(retained['completion.json']) == provenance.get('completion_sha256')
        response = retained.get('response')
        if response is not None:
            if response.lstrip().startswith((b'data:', b'event:')):
                report['response'] = stream_facts(response, dict(model='gpt-6.1-sol'))
            else:
                try:
                    value = strict_json(response)
                    error = value.get('error') if isinstance(value, dict) else None
                    report['response'] = dict(format='json', error_object=isinstance(error, dict),
                                              subscription_sharing_unavailable=isinstance(error, dict) and error.get('code') == 'subscription_sharing_user_unavailable',
                                              terminal_execution='unconfirmed')
                except (ValueError, TypeError, RecursionError):
                    report['response'] = dict(format='unknown', terminal_execution='unconfirmed')
        report['capture_ordering'] = order_facts(documents.get('request'), retained.get('checkpoint'))
        report['cleanup_confirmed_in_receipt'] = (receipt is not None and receipt.get('native_tool_cleanup') is True and
                                                  receipt.get('container_cleanup') in ('confirmed', 'stopped'))
        # A generic reconciliation contract is not a failed-transport accounting contract.
        contract = packet.get('recovery_contract', {}).get('failed_transport_accounting')
        if isinstance(contract, dict):
            report['failed_transport_accounting_contract'] = dict(available=True, supplied_sha256=sha(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()), installed_verified=False)
        report.update(status='structural-diagnostics', reason='available-originals-inspected')
        if len(json.dumps(report).encode()) > MAX_OUTPUT: raise ValueError('output-bound')
        return report
    except Hold:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        # Never echo exceptions (which may contain private data).
        return dict(report, status='inconclusive', reason='malformed-conflicting-or-bounded-evidence')
