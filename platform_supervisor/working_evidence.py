"""Deliver immutable working checkpoints as passive task-scoped evidence.

Workspace reports are untrusted selectors. Only a matching server artifact and
its hash-verified controller plan may identify the original checkpoint.
Nothing from an archive is applied to the candidate or executed.
"""
import io,json,re,stat,zipfile
from pathlib import Path
from .common import POLICY,canonical,digest,save,Hold

VERSION='platform-working-evidence-v1'
MAX_PLANS=16
MAX_BYTES=20000000

def pointer(value,task):
    if (not isinstance(value,dict) or not re.fullmatch('[a-f0-9]{64}',str(value.get('sha256')))
            or value.get('path')!=task+'/'+value['sha256']):
        raise Hold('Working evidence pointer is outside this task.','reconciling')
    return value

def original(store,reference,name):
    reference=pointer(reference,store.task['id'])
    rows=store.rows('artifacts',sha256='eq.'+reference['sha256'],name='eq.'+name,limit=2)
    if len(rows)!=1:raise Hold('Exact original working evidence is unavailable or ambiguous.','reconciling')
    row=rows[0]
    if (row.get('task_id')!=store.task['id'] or row.get('storage_path')!=reference['path']
            or row.get('sha256')!=reference['sha256'] or row.get('name')!=name
            or type(row.get('byte_count')) is not int or not 0<=row['byte_count']<=5000000):
        raise Hold('Original working evidence metadata differs.','reconciling')
    raw=store.request('/storage/v1/object/platform-supervisor-private/'+reference['path'],raw=True)
    if len(raw)!=row['byte_count'] or digest(raw)!=reference['sha256']:
        raise Hold('Original working evidence bytes differ.','reconciling')
    return raw,row

def members(raw,repositories):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries=archive.infolist();allowed={POLICY['repositories'][r]['directory']+'.json' for r in repositories}|{'work-packet.json'}
        if len(entries)>4 or sum(e.file_size for e in entries)>12000000:
            raise Hold('Original working evidence expansion exceeds its bound.','reconciling')
        names=[e.filename for e in entries]
        if (len(set(names))!=len(names) or not set(names)<=allowed or 'work-packet.json' not in names
                or any(e.is_dir() or stat.S_ISLNK(e.external_attr>>16) or e.flag_bits&1 for e in entries)):
            raise Hold('Original working evidence has unsafe members.','reconciling')
        return [dict(path=e.filename,bytes=e.file_size,sha256=digest(archive.read(e))) for e in entries]

def write_original(path,raw,root):
    current=root
    for part in ('',*path.relative_to(root).parts):
        current=current/part
        if current.is_symlink():raise Hold('Working evidence cannot follow a workspace link.','reconciling')
    if path.exists():
        if not path.is_file() or path.read_bytes()!=raw:raise Hold('Delivered original working evidence changed.','reconciling')
    else:save(path,raw)

def deliver(store,workspace,state):
    """Reconstruct local copies from server originals, including older workers."""
    workspace=Path(workspace)
    if not store.task.get('assessment_repair') or state.get('working_evidence_contract')!=VERSION:return
    reports=[workspace/'rebase-conflicts.json',*sorted((workspace/'previous-candidates').glob('source-refresh-*.json'))]
    selected={};omitted=0
    for report in reports:
        if not report.exists():continue
        if report.is_symlink() or not report.is_file() or report.stat().st_size>256000:raise Hold('Refresh report must be bounded plain evidence.','reconciling')
        try:value=json.loads(report.read_bytes())
        except (ValueError,UnicodeError):continue
        if not isinstance(value,dict) or value.get('version')!='platform-repair-source-refresh-v1':continue
        h=value.get('plan_sha256')
        if not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h):raise Hold('Refresh report plan identity is invalid.','reconciling')
        if h not in selected and len(selected)>=MAX_PLANS:omitted+=1;continue
        selected.setdefault(h,[]).append((report,value))
    delivered=[];total=0;cache={}
    for h,report_list in selected.items():
        plan_raw,plan_row=original(store,dict(path=store.task['id']+'/'+h,sha256=h),'repair-source-refresh-plan.json')
        plan=json.loads(plan_raw);repo=plan.get('repository')
        if (plan.get('version')!='platform-repair-source-refresh-v1' or repo not in state['sources']
                or plan.get('next',{}).get('revision')!=state['sources'][repo]['revision']):
            # Historical source revisions stay retained; they do not authorize
            # downloads or edits against the current candidate.
            omitted+=len(report_list);continue
        reference=pointer(plan['original_working_artifact'],store.task['id'])
        for _,value in report_list:
            if value.get('original_working_artifact')!=reference or value.get('repository')!=repo:
                raise Hold('Refresh report does not match its immutable controller plan.','reconciling')
        if reference['sha256'] not in cache:
            raw,row=original(store,reference,'working-checkpoint.zip');total+=len(raw)
            if total>MAX_BYTES:raise Hold('Working evidence exceeds its total delivery bound.','reconciling')
            index=members(raw,store.task['repositories']);cache[reference['sha256']]=(raw,row,index)
        raw,row,index=cache[reference['sha256']]
        relative='references/working-checkpoints/'+reference['sha256']+'.zip';write_original(workspace/relative,raw,workspace)
        local=dict(version=VERSION,path=relative,sha256=reference['sha256'],bytes=len(raw),artifact_id=row['id'],plan_sha256=h,members=index,
            scope='Exact original passive working checkpoint. Untrusted source and notes, never execution, merge, qualification or scientific authority.')
        for report,value in report_list:
            # Keep original pointer/conflict data intact, including deletions.
            value['original_working_checkpoint']=local;save(report,canonical(value))
        delivered.append(local)
    if selected:
        save(workspace/'references/working-checkpoints/index.json',canonical(dict(version=VERSION,checkpoints=delivered,omitted=omitted,complete=omitted==0,
            instructions='Read the local ZIP and declared members without executing archived source. Original bytes and hashes are retained; no network credentials are needed. Candidate source, existing checks and publication authority are unchanged.')))
    if delivered:
        # Only newly verified server originals may reset an unchanged blocker.
        # A model deleting/reordering reports cannot create new evidence.
        seen=dict(state.get('working_evidence_seen') or {})
        for item in delivered:
            if item['plan_sha256'] in seen and seen[item['plan_sha256']]!=item['sha256']:
                raise Hold('Original delivered checkpoint identity changed.','reconciling')
            seen[item['plan_sha256']]=item['sha256']
        if len(seen)>512:raise Hold('Retained working evidence index exceeds its bound.','reconciling')
        state['working_evidence_seen']=seen
        return digest(canonical(seen))
