"""Passive, bounded views of original research checkpoints and native history.

Nothing is extracted to disk or executed. The caller verifies the original
artifact, then applies the existing credential/contact redaction boundary.
"""
import gzip
import io
import json
from pathlib import PurePosixPath
import stat
import zipfile
import zlib
import time
from .common import canonical, digest, Hold

HISTORY_BYTES = 16000000
HISTORY_PAIRS = 8
PROJECT_FILES = 32
MEMBERS = (
    'project/experiment.py', 'project/repair_test.py', 'project/build-evidence.json',
    'repair-validation-current.json', 'finish-repair.py', 'read-retained.py',
    'work-packet.json', 'delivery-proofs.json', 'context-index.json',
    'builder-continuation.json', 'native-context-budget.json', 'runner-contract.json',
    'repair-test-plan.json', 'project/repair-test-plan.json', 'candidate.json',
    'project/candidate.json',
)


def fragment(raw, maximum=24000):
    record = dict(original_sha256=digest(raw), original_bytes=len(raw), complete=len(raw)<=maximum)
    if record['complete']:
        try: record['value'] = json.loads(raw)
        except (ValueError, UnicodeError): record['text'] = raw.decode('utf-8', 'replace')
    else:
        record.update(representation='bounded-original-excerpt', excerpt=raw[:maximum].decode('utf-8', 'replace'))
    return record


def history_view(raw):
    history = json.loads(raw)
    if not isinstance(history, list): raise Hold('Original native history must be an array.', 'reconciling')
    records = {}; calls = []
    for ordinal, item in enumerate(history):
        if (not isinstance(item, dict) or item.get('type') not in ('function_call','function_call_output')
                or not isinstance(item.get('call_id'),str) or not 1<=len(item['call_id'])<=200):
            raise Hold('Original native history has an unsupported record.', 'reconciling')
        key = (item['type'], item['call_id'])
        if key in records: raise Hold('Original native history has duplicate call identities.', 'reconciling')
        records[key] = (ordinal, item)
        if item['type']=='function_call': calls.append(item['call_id'])
    selected = calls[-HISTORY_PAIRS:]
    pairs = []
    for ident in selected:
        request = records[('function_call',ident)]
        output = records.get(('function_call_output',ident))
        pairs.append(dict(call_id=ident, request=dict(ordinal=request[0], **fragment(canonical(request[1]),12000)),
            output=dict(ordinal=output[0], **fragment(canonical(output[1]),12000)) if output else None,
            output_recorded=output is not None))
    orphan_outputs=sum(kind=='function_call_output' and ('function_call',key) not in records for kind,key in records)
    return dict(original_sha256=digest(raw), original_bytes=len(raw), records=len(history),
        requests=len(calls), paired_requests=sum(('function_call_output',key) in records for key in calls),
        orphan_outputs=orphan_outputs,complete=len(selected)==len(calls) and orphan_outputs==0, shown_pairs=len(pairs), pairs=pairs,
        scope='Exact retained tool records with original ordinals and hashes; excerpts/redactions are labeled. Missing outputs remain missing. No command was executed or replayed.')


def controller_response_view(raw):
    """Passive terminal/output inspection, never an admission or replay proof."""
    import siwc_protocol as wire
    result=dict(original_sha256=digest(raw),original_bytes=len(raw),
        scope='Original data-only response diagnostics. A terminal response is not an accepted decision, reconciliation authority or scientific validation.')
    try:terminal=wire.read_stream(io.BytesIO(raw),io.BytesIO(),time.monotonic()+5,1048576)
    except (ValueError,KeyError,TypeError,AttributeError,UnicodeError) as error:
        result.update(terminal_complete=False,error_type=type(error).__name__,raw_excerpt=fragment(raw,12000));return result
    result.update(terminal_complete=True,terminal_sha256=digest(canonical(terminal)),
        terminal={k:terminal.get(k) for k in ('id','model','status','usage')},
        output_types=[v.get('type') for v in terminal.get('output',[])],
        assistant_outputs=[fragment(canonical(v),48000) for v in terminal.get('output',[]) if v.get('type')=='message'])
    try:wire.structured_response(terminal)
    except (ValueError,KeyError,TypeError) as error:
        from scheduler import public_reason
        result.update(decision_valid=False,validation_error=public_reason(str(error),maximum=1200))
    else:result['decision_valid']=True
    return result


def checkpoint_view(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries)>128 or sum(i.file_size for i in entries)>4000000:
                raise Hold('Research checkpoint exceeds its original expansion bound.')
            names = set()
            for item in entries:
                path = PurePosixPath(item.filename)
                if (item.filename in names or path.is_absolute() or '..' in path.parts
                        or '\\' in item.filename or '\x00' in item.orig_filename
                        or stat.S_ISLNK(item.external_attr>>16) or item.flag_bits&1):
                    raise Hold('Unsafe original research checkpoint; no source was executed.')
                names.add(item.filename)
            def read(name, maximum):
                info = archive.getinfo(name)
                if info.is_dir() or info.file_size>maximum: raise Hold('Original diagnostic member exceeds its bound.', 'reconciling')
                with archive.open(info) as source: value = source.read(maximum+1)
                if len(value)!=info.file_size or len(value)>maximum: raise Hold('Original diagnostic member size differs.', 'reconciling')
                return value
            files = {}; project_files = {}
            projects=sorted(name for name in names if name.startswith('project/') and not archive.getinfo(name).is_dir())
            selected=[name for name in projects if PurePosixPath(name).suffix.lower() in ('.py','.json','.toml','.sh','.md','.txt')
                and not any(part.startswith('.') for part in PurePosixPath(name).parts)
                and archive.getinfo(name).file_size<=1000000][:PROJECT_FILES]
            for name in dict.fromkeys((*MEMBERS,*selected)):
                if name not in names: continue
                value = read(name,1000000)
                files[name] = fragment(value)
                if name in MEMBERS[:4] or name in selected:
                    project_files[name] = value.decode('utf-8','replace') if len(value)<=24000 else dict(available=False,bytes=len(value),reason='Source exceeds the bounded diagnostic view; original checkpoint retained.')
            result = dict(project_files=project_files, delivered_files=files,
                project_file_coverage=dict(total=len(projects),shown=len(set(projects)&set(files)),
                    omitted=[dict(path=n,bytes=archive.getinfo(n).file_size,reason='Outside the approved source-type, size or file-count bound.') for n in projects if n not in files]),
                scope='Diagnostic copies only. Untrusted evidence, never execution authority, an executable platform patch or a scientific result.')
            if 'native-tool-history.json' in names:
                encoded = read('native-tool-history.json',1000000)
                index = json.loads(encoded)
                if isinstance(index,list): history = encoded
                else:
                    if (not isinstance(index,dict) or index.get('version')!='builder-history-gzip-v1'
                            or index.get('path')!='native-tool-history.json.gz'
                            or type(index.get('bytes')) is not int or not 0<=index['bytes']<=HISTORY_BYTES
                            or type(index.get('records')) is not int or index['records']<0):
                        raise Hold('Original native history index is invalid.', 'reconciling')
                    compressed = read('native-tool-history.json.gz',1000000)
                    if digest(compressed)!=index.get('compressed_sha256'): raise Hold('Compressed original history hash differs.', 'reconciling')
                    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source: history = source.read(HISTORY_BYTES+1)
                    if len(history)>HISTORY_BYTES or len(history)!=index['bytes'] or digest(history)!=index.get('sha256'):
                        raise Hold('Expanded original history identity differs.', 'reconciling')
                    if len(json.loads(history))!=index['records']: raise Hold('Original history coverage differs.', 'reconciling')
                result['native_history'] = history_view(history)
            else:
                result['native_history'] = dict(available=False,reason='No native history member was retained in this checkpoint.')
            return result
    except Hold: raise
    except (ValueError, KeyError, OSError, EOFError, UnicodeError, zipfile.BadZipFile, zlib.error) as error:
        raise Hold('Original checkpoint diagnostic could not be verified: '+type(error).__name__, 'reconciling') from None
