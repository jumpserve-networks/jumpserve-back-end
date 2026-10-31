"""Fixed checks in a credential-free container; no host shell evaluation."""
import os
import datetime as dt
from pathlib import Path
import subprocess
import time
import json
import re
from .common import POLICY, canonical, digest, Hold
from .source import snapshot, extract

def prepare_runtime_inputs(store,directory,image,github):
    """Stage only hash-checked runtime prerequisites at frozen public revisions."""
    from .common import save
    names=('research-workflow-runtime.json','real-world-runtime.json','openclaw-worker-runtime.json','autonomy-pdf-runtime.json')
    fingerprint=digest(canonical({name:digest((directory/name).read_bytes()) for name in names}))
    marker=store.spool/store.task['id']/'dependencies'/'runtime-inputs'
    if marker.exists() and marker.read_text()==fingerprint and all((directory/name).is_dir() for name in ('.runtime-research','.runtime-backend','.runtime-openclaw','.runtime-autonomy-python')):return
    pins=[(json.loads((directory/'research-workflow-runtime.json').read_bytes())['source_revision'],'.runtime-research'),(json.loads((directory/'real-world-runtime.json').read_bytes())['revision'],'.runtime-backend')]
    for revision,target in pins:
        if not re.fullmatch('[a-f0-9]{40}',revision):raise Hold('Pinned runtime input is malformed.')
        extract(github.archive('jumpserve-networks/jumpserve-back-end',revision),directory/target)
    commands=[['python3','-B','bin/prepare-research-runtime.py','--backend','.runtime-research'],['python3','-B','bin/prepare-openclaw-worker.py','--backend','.runtime-research'],['python3','-B','bin/prepare-autonomy-runtime.py']]
    for number,command in enumerate(commands):
        flags=store.rpc('lease')
        if flags['cancel_requested'] or flags['paused']:raise Hold('Runtime preparation stopped at an owner boundary.','implementing')
        remaining=(dt.datetime.fromisoformat(flags['deadline_at'].replace('Z','+00:00'))-dt.datetime.now(dt.timezone.utc)).total_seconds()
        if remaining<10:raise Hold('Elapsed-time limit reached before runtime preparation.','deadline')
        name='js-platform-'+store.task['id'].replace('-','')+'-runtime-'+str(number)
        try:
            result=subprocess.run(['docker','run','--rm','--name',name,'--network','bridge','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--memory','1g','--user',str(os.getuid())+':'+str(os.getgid()),'--env','HOME=/tmp','--mount','type=bind,source='+str(directory.resolve())+',target=/workspace','--workdir','/workspace',image,*command],capture_output=True,timeout=min(300,remaining-5))
        except subprocess.TimeoutExpired:
            try:subprocess.run(['docker','rm','-f',name],check=True,capture_output=True,timeout=30)
            except Exception:
                from .native import CleanupUnknown
                raise CleanupUnknown('Runtime preparation cleanup needs reconciliation.','reconciling') from None
            raise Hold('Runtime preparation timed out; pinned inputs remain retained.','investigating') from None
        store.artifact('runtime-preparation-'+str(number)+'.json',canonical(dict(command=command,exit_code=result.returncode,stdout_tail=result.stdout[-12000:].decode('utf-8','replace'),stderr_tail=result.stderr[-12000:].decode('utf-8','replace'),credential_free=True)))
        if result.returncode:raise Hold('Pinned runtime prerequisite failed its preparation or hash check.','investigating')
    save(marker,fingerprint.encode())

def prepare_dependencies(store,repository,directory,image,github=None):
    directory=Path(directory);lock=directory/'package-lock.json'
    if repository=='jumpserve-networks/jumpserve-infra':
        if github is None:raise Hold('Trusted pinned runtime acquisition is unavailable.')
        prepare_runtime_inputs(store,directory,image,github)
    if not lock.is_file():return None
    from .common import save
    sha=digest(lock.read_bytes());marker=store.spool/store.task['id']/'dependencies'/repository.split('/')[1]
    if marker.exists() and marker.read_text()==sha and (directory/'node_modules').is_dir():return None
    flags=store.rpc('lease')
    if flags['cancel_requested'] or flags['paused']:raise Hold('Dependency preparation stopped at an owner control boundary.','implementing')
    remaining=(dt.datetime.fromisoformat(flags['deadline_at'].replace('Z','+00:00'))-dt.datetime.now(dt.timezone.utc)).total_seconds()
    if remaining<10:raise Hold('Elapsed-time limit reached before dependency preparation.','deadline')
    name='js-platform-'+store.task['id'].replace('-','')+'-dependencies'
    try:
        result=subprocess.run(['docker','run','--rm','--name',name,'--network','bridge','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--memory','2g','--user',str(os.getuid())+':'+str(os.getgid()),'--env','HOME=/tmp','--mount','type=bind,source='+str(directory.resolve())+',target=/workspace','--workdir','/workspace',image,'npm','ci','--ignore-scripts'],capture_output=True,timeout=min(600,remaining-5))
    except subprocess.TimeoutExpired:
        try:subprocess.run(['docker','rm','-f',name],check=True,capture_output=True,timeout=30)
        except Exception:
            from .native import CleanupUnknown
            raise CleanupUnknown('Dependency container cleanup needs reconciliation.','reconciling') from None
        raise Hold('Public dependency installation timed out; retry from the retained lockfile.','investigating') from None
    receipt=dict(repository=repository,package_lock_sha256=sha,exit_code=result.returncode,stdout_tail=result.stdout[-12000:].decode('utf-8','replace'),stderr_tail=result.stderr[-12000:].decode('utf-8','replace'),credential_free=True)
    store.artifact('dependency-preparation-'+repository.split('/')[1]+'.json',canonical(receipt))
    if result.returncode:return receipt
    save(marker,sha.encode());return None

def execute(store, repository, directory, image):
    before=snapshot(directory);results=[]
    for number,command in enumerate(POLICY['repositories'][repository]['checks']):
        flags=store.rpc('lease')
        if flags['cancel_requested']:raise Hold('Cancellation honored between fixed checks.','cancelled')
        if flags['paused']:raise Hold('Owner paused admission between fixed checks.','testing')
        remaining=(dt.datetime.fromisoformat(flags['deadline_at'].replace('Z','+00:00'))-dt.datetime.now(dt.timezone.utc)).total_seconds()
        if remaining<10:raise Hold('Elapsed-time limit reached before another check.','deadline')
        name='js-platform-'+store.task['id'].replace('-','')+'-check-'+str(number)
        # Dependency installation has outbound access in its own container.
        # EC2 installation enforces IMDSv2 hop-limit 1; no host socket, env,
        # publisher key, cloud key or home directory is mounted.
        network='bridge' if command[:2]==['npm','ci'] else 'none'
        args=['docker','run','--rm','--name',name,'--network',network,'--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--memory','3g','--memory-swap','3g','--cpus','2','--user',str(os.getuid())+':'+str(os.getgid()),'--env','HOME=/tmp','--env','CI=true','--env','NODE_OPTIONS=--max-old-space-size=1536','--env','NEXT_PUBLIC_SUPABASE_URL=https://regphejnlvfpyokpniny.supabase.co','--env','NEXT_PUBLIC_SUPABASE_ANON_KEY=platform-build-placeholder','--env','NEXT_PUBLIC_SITE_URL=https://jumpserve.quaint-lab.org','--mount','type=bind,source='+str(Path(directory).resolve())+',target=/workspace','--workdir','/workspace',image,*command]
        started=time.monotonic()
        try:
            output=subprocess.run(args,capture_output=True,timeout=min(900,remaining-5))
            stdout=store.artifact('check-'+repository.split('/')[1]+'-'+str(number)+'-stdout.txt',output.stdout[-4000000:])
            stderr=store.artifact('check-'+repository.split('/')[1]+'-'+str(number)+'-stderr.txt',output.stderr[-4000000:])
            results.append(dict(command=command,exit_code=output.returncode,stdout_artifact=stdout['id'],stderr_artifact=stderr['id'],stdout_tail=output.stdout[-12000:].decode('utf-8','replace'),stderr_tail=output.stderr[-12000:].decode('utf-8','replace'),wall_seconds=time.monotonic()-started,output_tail_limit_bytes=4000000))
            if output.returncode:break
        except subprocess.TimeoutExpired:
            try:subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30,check=True)
            except Exception:
                from .native import CleanupUnknown
                raise CleanupUnknown('Fixed-check container cleanup needs reconciliation.','reconciling') from None
            results.append(dict(command=command,exit_code=None,reason='Fixed check exceeded 900 seconds.'));break
    after=snapshot(directory)
    passed=all(r['exit_code']==0 for r in results) and len(results)==len(POLICY['repositories'][repository]['checks']) and before==after
    receipt=dict(version='platform-checks-v1',repository=repository,passed=passed,tree_sha256=digest(canonical(before)),source_unchanged=before==after,results=results,reviewer=dict(type='automated',independent=False))
    artifact=store.artifact('checks-'+repository.split('/')[1]+'.json',canonical(receipt))
    return receipt,artifact
