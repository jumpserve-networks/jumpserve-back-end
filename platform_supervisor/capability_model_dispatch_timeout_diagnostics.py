"""Read-only retained timeout diagnostics; activated only by frozen self-update.

No original text is executed. Missing evidence is never an admission decision.
"""
import hashlib
import json
import math
import re
from .common import Hold

CAPABILITY = {
    'version': 'platform-capability-pack-v1',
    'id': 'model-dispatch-timeout-diagnostics',
    'revision': 1,
    'description': 'Task-bound retained model timeout evidence and explicit diagnostic gaps.',
    'instructions': 'After verified supervisor installation, the controller invokes collect(context) against the indexed original evidence packet. Read the resulting references/capabilities/diagnostics-model-dispatch-timeout-diagnostics.json. Optionally pipe that JSON into python3 describe.py for a compact availability summary. This tool never dispatches, reconciles or permits replay. A finished task is reported separately from the unresolved dispatch. Missing historical CLI outputs or source identity cannot be recovered by inference. Installation requires frozen qualification and exact installed source proof.',
    'files': {
        'describe.py': '"""Summarize a retained diagnostic document on stdin, without external IO."""\nimport json, sys\nraw = sys.stdin.buffer.read(1000001)\nif len(raw) > 1000000:\n    raise ValueError("Diagnostic document exceeds bound")\nvalue = json.loads(raw)\nvalue = value.get("view", value)\nif value.get("version") != "model-dispatch-timeout-diagnostics-v1":\n    raise ValueError("Expected timeout diagnostic document")\nprint(json.dumps({key: value.get(key) for key in ("version", "assessment_id", "dispatch_id", "availability", "gaps", "replay_authorized")}))\n',
        'README.md': 'Read-only diagnostics for retained model-dispatch timeouts. The collector reads only the trusted task packet and indexed hash-verified originals. Its output records absence, not proof that an operation did not occur. provider_request_sent=false and provider_calls=0 are receipt observations, never reconciliation or safe replay. No scientific completion or installation is established by fixtures.\n'
    }
}

ASSESSMENT = 'c84744c5-aeb8-460c-9803-22ad820603bf'
DISPATCH = '1d91810f-4caa-5676-9e44-a1079c874566'
MAX_ORIGINAL = 1048576


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _small(value):
    """Only bounded scalar facts; never entire prompts, credentials or logs."""
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float) and abs(value) <= 1e15 and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= 200:
        return value
    return None


def _rows(packet, key):
    rows = packet.get(key, [])
    if not isinstance(rows, list) or len(rows) > 256 or any(not isinstance(r, dict) for r in rows):
        raise ValueError('Invalid or oversized diagnostic index: ' + key)
    return rows


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate original JSON field.')
        result[key] = value
    return result


def collect(context):
    """Passive task-scoped view. Evidence persistence Holds propagate unchanged."""
    packet = context.packet
    result = dict(version='model-dispatch-timeout-diagnostics-v1', assessment_id=ASSESSMENT,
                  dispatch_id=DISPATCH, availability='unavailable', gaps=[], replay_authorized=False,
                  scope='Retained evidence only; no reconciliation, replay, lease or scientific state mutation.')
    if not isinstance(packet, dict) or packet.get('assessment_id') != ASSESSMENT:
        result['gaps'].append('Requested assessment binding unavailable or mismatched.')
        return result
    try:
        blocker = packet.get('blocker') or {}
        if not isinstance(blocker, dict):
            raise ValueError('Invalid task blocker.')
        if blocker.get('source_kind') != 'dispatch' or blocker.get('source_id') != DISPATCH:
            raise ValueError('Requested dispatch is not the task-bound blocker.')
        matches = [r for r in _rows(packet, 'dispatches') if r.get('id') == DISPATCH]
        if len(matches) != 1:
            raise ValueError('Unique requested dispatch binding unavailable.')
        dispatch = matches[0]
        request_sha = dispatch.get('request_sha256')
        if not _sha(request_sha):
            raise ValueError('Original request hash binding unavailable.')
        provenance = dispatch.get('provenance') or {}
        if not isinstance(provenance, dict):
            raise ValueError('Invalid dispatch provenance.')
        artifacts = _rows(packet, 'artifacts')
        declared = _rows(provenance, 'artifacts')
        logical = [r for r in declared if r.get('name') == 'logical.json']
        receipt = [r for r in declared if r.get('name') == 'receipt.json']
        if len(logical) != 1 or logical[0].get('sha256') != request_sha or len(receipt) != 1:
            raise ValueError('Unique logical request and receipt bindings unavailable or mismatched.')

        def binding(row):
            indexed = [a for a in artifacts if a.get('id') == row.get('id')]
            if (len(indexed) != 1 or not _sha(row.get('sha256')) or
                    indexed[0].get('sha256') != row['sha256'] or
                    not isinstance(indexed[0].get('provenance'), dict) or
                    indexed[0]['provenance'].get('dispatch_id') != DISPATCH):
                raise ValueError('Indexed original artifact dispatch/hash binding unavailable or mismatched.')
            size = indexed[0].get('byte_count')
            if type(size) is not int or not 0 <= size <= MAX_ORIGINAL:
                raise ValueError('Original exceeds diagnostic byte bound or has no size.')
            if row.get('byte_count') != size:
                raise ValueError('Original artifact size bindings differ.')
            return dict(artifact_id=row['id'], sha256=row['sha256'], byte_count=size)

        request_binding, receipt_binding = binding(logical[0]), binding(receipt[0])
        # context.original enforces persistence scope/hash; verify the returned
        # bytes as well so a malformed adapter cannot create a false observation.
        raw = context.original(receipt_binding['artifact_id'])
        if (not isinstance(raw, bytes) or len(raw) > MAX_ORIGINAL or
                len(raw) != receipt_binding['byte_count'] or
                hashlib.sha256(raw).hexdigest() != receipt_binding['sha256']):
            raise ValueError('Original receipt bytes do not match their binding.')
        try:
            observed = json.loads(raw, object_pairs_hook=_object)
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError('Original receipt is malformed JSON.') from None
        if not isinstance(observed, dict):
            raise ValueError('Original receipt must be an object.')
        transport = observed.get('context_transport')
        if not isinstance(transport, dict) or transport.get('original_logical_sha256') != request_sha:
            raise ValueError('Receipt original request hash binding unavailable or mismatched.')
        for key, expected in (('assessment_id', ASSESSMENT), ('dispatch_id', DISPATCH)):
            if key in observed and observed[key] != expected:
                raise ValueError('Receipt assessment/dispatch identity mismatch.')
        result.update(availability='receipt-observed', request=request_binding, receipt=receipt_binding,
                      dispatch_status=_small(dispatch.get('status')),
                      observed={key: _small(observed.get(key)) for key in
                                ('status', 'error_code', 'wall_seconds', 'provider_request_sent',
                                 'provider_calls', 'exit_code', 'returncode')},
                      runtime={key: _small(observed.get(key)) for key in
                               ('runtime', 'node_version', 'openclaw_version')})
        result['observed_timeout'] = observed.get('error_code') == 'runtime-TimeoutExpired'
        fingerprints = {key: observed[key] for key in
                        ('source_revision', 'runtime_source_revision', 'installed_source_sha256', 'runtime_source_sha256')
                        if isinstance(observed.get(key), str) and
                        re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', observed[key])}
        result['runtime_source_identity'] = fingerprints or None
        if not fingerprints:
            result['gaps'].append('Installed runtime source identity is not recorded in the retained receipt; version labels are not source proof.')
        outputs = {}
        for name in ('stdout.bin', 'stderr.bin'):
            rows = [r for r in declared if r.get('name') == name]
            if len(rows) > 1:
                raise ValueError('Ambiguous CLI output binding.')
            if rows:
                proof = binding(rows[0])
                data = context.original(proof['artifact_id'])
                if (not isinstance(data, bytes) or len(data) != proof['byte_count'] or
                        hashlib.sha256(data).hexdigest() != proof['sha256']):
                    raise ValueError('CLI original bytes differ from their binding.')
                # Do not disclose CLI text; retain exact proof and availability.
                outputs[name] = dict(availability='retained', **proof)
            else:
                outputs[name] = dict(availability='not-indexed')
                result['gaps'].append('No retained ' + name + ' indexed for this dispatch.')
        result['cli_outputs'] = outputs
        if observed.get('exit_code') is None and observed.get('returncode') is None:
            result['gaps'].append('Native CLI exit status is not recorded in the retained receipt.')
        task = packet.get('task') or {}
        if not isinstance(task, dict) or task.get('id') != packet.get('assessment_task_id') or not task.get('id'):
            raise ValueError('Current assessment task binding unavailable.')
        result['current_task'] = {key: _small(task.get(key)) for key in ('id', 'status', 'stage')}
        result['current_task']['terminal'] = task.get('status') in ('finished', 'failed', 'cancelled')
        reconciliations = [r for r in _rows(packet, 'model_reconciliations') if r.get('dispatch_id') == DISPATCH]
        for row in reconciliations:
            if (row.get('study_id') != ASSESSMENT or
                    row.get('task_id') != task['id'] or row.get('request_sha256') != request_sha):
                raise ValueError('Existing reconciliation task/request binding mismatched or unavailable.')
        result['existing_controller_reconciliation'] = {
            'availability': 'recorded' if reconciliations else 'not-in-packet',
            'records': [{key: _small(r.get(key)) for key in
                         ('dispatch_id', 'study_id', 'task_id', 'request_sha256', 'artifact_id', 'sha256', 'created_at')}
                        for r in reconciliations[:8]],
            'records_total_in_packet': len(reconciliations),
            'interpretation': 'Passive controller records only; not validated as replay admission.'}
        if not reconciliations:
            result['gaps'].append('No existing controller reconciliation for this dispatch in the bounded task packet.')
        result['gaps'].append('Provider counters and current task terminal state do not reconcile the original unknown dispatch or authorize replay.')
    except Hold:
        raise  # Persistence uncertainty must retain the controller's hold state.
    except ValueError as error:
        # Fail closed, discarding any partial observation on a binding error.
        return dict(version=result['version'], assessment_id=ASSESSMENT, dispatch_id=DISPATCH,
                    availability='unavailable', gaps=[str(error)], replay_authorized=False, scope=result['scope'])
    return result
