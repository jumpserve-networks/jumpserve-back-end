"""Task-bound observations only; never invoke the recovery adapter or its stores."""
import re
from .common import Hold
from .capability_retained_provider_outcome_diagnostics import canonical, sha, strict_json, stream_facts

CAPABILITY = {
    'version': 'platform-capability-pack-v1',
    'id': 'original-native-recovery-diagnostics',
    'revision': 1,
    'description': 'Read-only incident-bound original native spool evidence diagnostics.',
    'instructions': 'Only the verified installed controller invokes collect(context). Missing private evidence is unavailable, not absent. This report never reconciles, admits replacement, resumes an assessment or certifies science. Release via frozen supervisor-runtime self-update and verify installed revision.',
    'files': {'README.md': 'The installed collect(context) uses only context.packet and context.original. It never opens a spool, calls a cloud/database API, invokes recovery, or changes outcomes. The existing private controller must supply indexed hash-verified originals before spool observations are possible. Optional packet native_recovery_diagnostics is a reference to an indexed JSON original with version original-native-spool-observation-v1 and exact identity fields (assessment_id, assessment_task_id, incident_id, dispatch_id, call_id, number, request_sha256). It contains originals keyed request/completion/response, each an indexed id/sha256/byte_count reference; missing components must be in verified_missing to establish absence from a completed private observation. It may include recovery_source (an indexed exact dispatch_recovery.py original), scan (eligible boolean, transport_active boolean, cleanup_uncertain boolean), and last_scan (kind refusal/exception, detail string). No reference means unavailable, never absent. Diagnostics preserve uncertainty even with a terminal observation; a fixture or installation is not live recovery. Only hashes of private last_scan details are emitted, with indexed original provenance, not untrusted messages. The controller must retain the precise original privately.\n'}
}

ASSESSMENT = 'fe761052-6e57-4d47-a3de-163c6cad905b'
TASK = '2277f9be-ce58-5e9b-b383-b282c7913e45'
INCIDENT = '115f6e06-6bc4-47fe-848c-a07b042f9cff'
DISPATCH = '6bf4dac3-01d2-56e1-a273-bea290fe94d1'
CALL = 'e1dd7f5c-ab3b-44e1-a8e7-0129968f2f7a'
REQUEST = '3f91b5124edb8abcb6cdc1f6e1f3bd88c1ddfe2cf4c5cc710fbc3c6f96e0022f'
MAX_BYTES = 1048576
MAX_ROWS = 4096


def identity():
    return dict(assessment_id=ASSESSMENT, assessment_task_id=TASK, incident_id=INCIDENT,
                dispatch_id=DISPATCH, call_id=CALL, number=79, request_sha256=REQUEST)


def matches(value):
    return isinstance(value, dict) and all(type(value.get(k)) is type(v) and value[k] == v
                                          for k, v in identity().items())


def collect(context):
    report = dict(version='original-native-recovery-diagnostics-v1', **identity(),
                  status='unavailable', reason='identity-unavailable', sources={},
                  spool='unavailable', completion='unavailable', response='unavailable',
                  recovery_source_sha256=None, scan_eligibility='unavailable', last_scan=None,
                  reconciled=False, replacement_admitted=False, scientific_completion=False,
                  assessment_resumed=False)
    try:
        packet = context.packet
        if not isinstance(packet, dict):
            return report
        blocker = packet.get('blocker', {})
        if (packet.get('assessment_id') != ASSESSMENT or packet.get('assessment_task_id') != TASK or
            not packet.get('platform_task_id') or blocker.get('id') != INCIDENT or
            blocker.get('source_id') != DISPATCH or blocker.get('source_kind') != 'dispatch'):
            return report
        calls = packet.get('native_calls', [])
        dispatches = packet.get('dispatches', [])
        if len(calls) > MAX_ROWS or len(dispatches) > MAX_ROWS:
            return dict(report, reason='packet-row-bound')
        calls = [c for c in calls if c.get('id') == CALL]
        dispatches = [d for d in dispatches if d.get('id') == DISPATCH]
        if len(calls) != 1 or len(dispatches) != 1:
            return dict(report, reason='call-or-dispatch-unavailable-or-ambiguous')
        call = calls[0]
        if (call.get('dispatch_id') != DISPATCH or call.get('request_sha256') != REQUEST or
            type(call.get('number')) is not int or call['number'] != 79):
            return dict(report, status='mismatch', reason='call-identity-mismatch')
        for row in (call, dispatches[0]):
            if any(k in row and row[k] != v for k, v in (('study_id', ASSESSMENT), ('task_id', TASK))):
                return dict(report, status='mismatch', reason='ledger-identity-mismatch')
        report['reason'] = 'private-spool-observation-not-in-trusted-packet'
        report['remaining_prerequisite'] = ('Existing private controller must collect this incident original spool availability, completion and response originals, exact installed dispatch_recovery.py source and last recovery scan/refusal or exception into indexed hash-verified evidence. No direct sandbox access or inference is permitted.')
        ref = packet.get('native_recovery_diagnostics')
        if ref is None:
            return report
        index = {}
        artifacts = packet.get('artifacts', [])
        if len(artifacts) > MAX_ROWS:
            return dict(report, reason='artifact-row-bound')
        for row in artifacts:
            ident, digest = row.get('id'), row.get('sha256')
            if ident in index and index[ident] != digest:
                return dict(report, status='mismatch', reason='ambiguous-artifact-index')
            index[ident] = digest
        for row in packet.get('native_calls', []) + packet.get('dispatches', []):
            for prefix in ('request', 'response'):
                ident, digest = row.get(prefix+'_artifact_id'), row.get(prefix+'_sha256')
                if not ident or not digest:
                    continue
                if ident in index and index[ident] != digest:
                    return dict(report, status='mismatch', reason='ambiguous-artifact-index')
                index[ident] = digest

        def read(ref, name, maximum=MAX_BYTES):
            if not isinstance(ref, dict):
                raise ValueError('original-reference-unavailable')
            ident, digest, size = ref.get('id'), ref.get('sha256'), ref.get('byte_count')
            if (not isinstance(ident, str) or len(ident) > 100 or not isinstance(digest, str) or
                not re.fullmatch('[a-f0-9]{64}', digest) or index.get(ident) != digest):
                raise ValueError('original-not-indexed')
            if type(size) is not int or not 0 <= size <= maximum:
                raise ValueError('original-size-bound')
            raw = context.original(ident)
            if not isinstance(raw, bytes) or len(raw) != size or sha(raw) != digest:
                raise ValueError('original-hash-or-size-mismatch')
            report['sources'][name] = dict(artifact_id=ident, sha256=digest, bytes=size)
            return raw

        observation = strict_json(read(ref, 'observation', 32000))
        if not matches(observation):
            return dict(report, status='mismatch', reason='observation-identity-mismatch')
        if observation.get('version') != 'original-native-spool-observation-v1':
            return dict(report, reason='unsupported-observation-version')
        report['spool'] = 'private-observation-verified'
        source = observation.get('recovery_source')
        if source is not None:
            report['recovery_source_sha256'] = sha(read(source, 'recovery_source'))
        scan = observation.get('scan', {})
        report['scan_eligibility'] = 'unavailable'
        if scan.get('transport_active') is True or scan.get('cleanup_uncertain') is True:
            report['scan_eligibility'] = 'active-transport-or-cleanup-uncertainty'
        elif all(type(scan.get(k)) is bool for k in ('eligible', 'transport_active', 'cleanup_uncertain')):
            report['scan_eligibility'] = 'observed-eligible' if scan['eligible'] else 'observed-ineligible'
        last = observation.get('last_scan')
        if last is not None:
            if (not isinstance(last, dict) or last.get('kind') not in ('refusal', 'exception') or
                not isinstance(last.get('detail'), str) or len(last['detail'].encode()) > 8000):
                return dict(report, reason='last-scan-malformed-or-bound')
            report['last_scan'] = dict(kind=last['kind'], detail_sha256=sha(last['detail'].encode()),
                                       original_artifact_id=ref['id'])
        originals = observation.get('originals', {})
        missing = observation.get('verified_missing', [])
        if not isinstance(originals, dict) or not isinstance(missing, list) or any(x not in ('request', 'completion', 'response') for x in missing):
            return dict(report, reason='malformed-original-coverage')
        if any(k in originals for k in missing):
            return dict(report, status='mismatch', reason='conflicting-original-coverage')
        for name in ('completion', 'response'):
            if name not in originals:
                report[name] = 'verified-missing' if name in missing else 'unavailable'
        if any(name not in originals for name in ('request', 'completion', 'response')):
            return dict(report, reason='original-components-missing-or-unavailable')
        request_raw = read(originals['request'], 'request', 256000)
        if sha(request_raw) != REQUEST or originals['request']['id'] != call.get('request_artifact_id'):
            return dict(report, status='mismatch', reason='original-request-mismatch')
        request = strict_json(request_raw)
        if (request.get('model') != 'gpt-6.1-sol' or request.get('store') is not False or
            request.get('stream') is not True or request.get('previous_response_id')):
            return dict(report, status='mismatch', reason='request-contract-mismatch')
        completion = strict_json(read(originals['completion'], 'completion', 16000))
        if (completion.get('version') != 'builder-native-call-reconciliation-v1' or
            any(completion.get(k) != v for k, v in dict(call_id=CALL, dispatch_id=DISPATCH,
                task_id=TASK, study_id=ASSESSMENT, request_sha256=REQUEST,
                response_filename='call-79-response.bin', status='complete', response_received=True).items()) or
            type(completion.get('response_received')) is not bool or
            type(completion.get('http_status')) is not int or completion['http_status'] != 200):
            return dict(report, status='mismatch', reason='completion-identity-or-contract-mismatch')
        raw = read(originals['response'], 'response')
        digest = sha(raw)
        if (digest != completion.get('response_sha256') or
            call.get('response_sha256') not in (None, digest) or
            call.get('response_artifact_id') not in (None, originals['response']['id'])):
            return dict(report, status='mismatch', reason='response-hash-or-identity-mismatch')
        report['completion'] = 'hash-and-identity-verified'
        report['response'] = 'hash-verified'
        report['stream'] = stream_facts(raw, request)
        # Structural stream inspection is NOT builder.validate_calls, cleanup or reconciliation.
        return dict(report, status='observed', reason='originals-structurally-inspected-not-reconciled')
    except Hold:
        raise
    except ValueError as error:
        reason = str(error)
        if reason not in ('original-reference-unavailable', 'original-not-indexed', 'original-size-bound', 'original-hash-or-size-mismatch'):
            reason = 'malformed-or-unavailable-private-evidence'
        return dict(report, status='unavailable', reason=reason)
    except (TypeError, KeyError, AttributeError, RecursionError):
        return dict(report, status='unavailable', reason='malformed-or-unavailable-private-evidence')
