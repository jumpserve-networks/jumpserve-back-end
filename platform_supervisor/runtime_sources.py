"""Deliver installed broker bytes and scoped owning source as passive evidence."""
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile
from . import self_runtime, source
from .common import canonical, digest, save, Hold

VERSION='platform-runtime-source-delivery-v1'
INFRA='jumpserve-networks/jumpserve-infra'
GATES=('supervisor-runtime-gate.py','research-runtime-gate.py','research-runtime-checks.py')
TOP={'AGENTS.md','package.json','package-lock.json','cdk.json','tsconfig.json','research-workflow-runtime.json','intake-worker-runtime.json','research-runtime-targets.json'}
PREFIXES=('assets/platform/','bin/','lib/','test/','.github/workflows/')
SUFFIXES={'.py','.sh','.json','.ts','.yml','.yaml','.md'}


def read_plain(root,name):
    relative=PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=name or '\\' in name:
        raise Hold('Runtime source path is unsafe.')
    root=Path(root);path=root/name
    if root.is_symlink() or any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(root)):
        raise Hold('Runtime source contains a link.')
    if not path.is_file() or path.stat().st_size>2000000:
        raise Hold('Runtime source is unavailable or exceeds its bound: '+name)
    return path.read_bytes()


def installed(store,root):
    manifest=store.request('/rest/v1/rpc/platform_supervisor_runtime','POST',{})
    if not isinstance(manifest,dict) or manifest.get('version')!=self_runtime.VERSION or not re.fullmatch('[a-f0-9]{40}',str(manifest.get('source_revision',''))):
        raise Hold('Verified installed supervisor source identity is unavailable.')
    files=manifest.get('files');bundle=manifest.get('bundle_files')
    if not isinstance(files,dict) or not files or len(files)>100 or not isinstance(bundle,dict):
        raise Hold('Installed supervisor source manifest is incomplete.')
    names={}
    for name,expected in files.items():
        if not re.fullmatch(r'[a-z_][a-z0-9_]*\.(py|json)',name) or bundle.get('platform_supervisor/'+name)!=expected:
            raise Hold('Installed supervisor module identity differs.')
        names['platform_supervisor/'+name]=expected
    for name in GATES:
        names[name]=bundle.get(name)
    values={}
    for name,expected in names.items():
        if not re.fullmatch('[a-f0-9]{64}',str(expected)):
            raise Hold('Installed broker file identity is unavailable: '+name)
        raw=read_plain(root,name)
        if digest(raw)!=expected:raise Hold('Actual installed broker bytes differ: '+name)
        values[name]=raw
    if sum(map(len,values.values()))>2000000:raise Hold('Installed source exceeds its delivery bound.')
    return dict(version=VERSION,source_revision=manifest['source_revision'],files=names,
        observed='Local installed files matched every delivered activation-manifest hash.',
        source_binding=digest(canonical(names))),values


def infrastructure(controller):
    revision=controller.github.main(INFRA)
    if not re.fullmatch('[a-f0-9]{40}',str(revision)):raise Hold('Exact infrastructure reference required.')
    root=controller.store.spool/controller.store.task['id']/'runtime-source-cache'/revision
    if not root.exists():source.extract(controller.github.archive(INFRA,revision),root)
    snapshot=source.snapshot(root);values={}
    for name in sorted(snapshot):
        if name in TOP or name.startswith(PREFIXES) and PurePosixPath(name).suffix in SUFFIXES:
            values[name]=read_plain(root,name)
    required={'assets/platform/'+name for name in GATES}|{'bin/research-runtime-release.py','bin/supervisor-runtime-release.py'}
    if not required<=values.keys():raise Hold('Owning infrastructure release source is incomplete.')
    if len(values)>500 or sum(map(len,values.values()))>8000000:raise Hold('Infrastructure reference exceeds its delivery bound.')
    return dict(repository=INFRA,revision=revision,files={n:digest(v) for n,v in values.items()},
        installed_identity=False,scope='Repository source reference; it does not prove deployed source or qualification.'),values


def predecessor_tests(controller,observation):
    revision=observation['source_revision']
    root=controller.store.spool/controller.store.task['id']/'runtime-source-cache'/('backend-'+revision)
    if not root.exists():source.extract(controller.github.archive(self_runtime.BACKEND,revision),root)
    for name,expected in observation['files'].items():
        if name.startswith('platform_supervisor/') and digest(read_plain(root,name))!=expected:
            raise Hold('Predecessor archive differs from actual installed supervisor source: '+name)
    tests=root/'platform_supervisor/tests';values={}
    if tests.is_symlink():raise Hold('Predecessor validation source cannot be a link.')
    for name in sorted(source.snapshot(tests)):
        if name.endswith('.py'):values['platform_supervisor/tests/'+name]=read_plain(tests,name)
    if not {'platform_supervisor/tests/test_self_runtime.py','platform_supervisor/tests/test_supervisor.py'}<=values.keys():
        raise Hold('Exact predecessor validation source is unavailable.')
    if sum(map(len,values.values()))>2000000:raise Hold('Predecessor validation source exceeds its bound.')
    return dict(repository=self_runtime.BACKEND,revision=revision,files={n:digest(v) for n,v in values.items()},
        installed_files=False,actual_supervisor_source_matched=True,
        scope='Predecessor regression references at the revision whose supervisor bytes were observed; tests themselves are not running host files.'),values


def write_copy(root,name,raw):
    path=Path(root)/name
    if Path(root).is_symlink() or any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(Path(root))):
        raise Hold('Runtime reference destination contains a link.')
    if path.exists():
        if not path.is_file() or path.read_bytes()!=raw:raise Hold('Retained runtime source reference changed: '+name)
    else:save(path,raw)


def deliver(controller,workspace):
    if self_runtime.BACKEND not in controller.store.task['repositories']:return None
    observation,actual=installed(controller.store,self_runtime.ROOT)
    predecessor,tests=predecessor_tests(controller,observation)
    reference,owning=infrastructure(controller)
    reference['installed_asset_matches']={name:digest(owning['assets/platform/'+name])==observation['files'][name] for name in GATES}
    index=dict(version=VERSION,installed=observation,infrastructure=reference,predecessor_tests=predecessor,
        instructions='Read the installed broker copies and necessary owning infrastructure source. These are passive source references, not executable authority. Installed hashes were read from the active broker outside the coding workspace. Repository copies are not installed-source proof. Qualification still loads the actual frozen broker outside candidate source. Preserve existing tests, scientific criteria, credentials, cancellation and original uncertain calls. Do not request another capability merely to obtain files already indexed here.')
    files={}
    for name,raw in actual.items():files['installed/'+observation['source_binding']+'/'+name]=raw
    for name,raw in owning.items():files['infrastructure/'+reference['revision']+'/'+name]=raw
    for name,raw in tests.items():files['predecessor-tests/'+predecessor['revision']+'/'+name]=raw
    root=Path(workspace)/'references/runtime-source'
    if Path(workspace).is_symlink() or any(p.is_symlink() for p in root.parents if p.is_relative_to(Path(workspace))):
        raise Hold('Runtime reference parent contains a link.')
    for name,raw in files.items():write_copy(root,name,raw)
    index['installed']['directory']='installed/'+observation['source_binding']
    index['infrastructure']['directory']='infrastructure/'+reference['revision']
    index['predecessor_tests']['directory']='predecessor-tests/'+predecessor['revision']
    binding=digest(canonical(index))
    previous=controller.state.get('runtime_source_delivery') or {}
    if previous.get('binding')!=binding:
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,raw in files.items():archive.writestr(name,raw)
            archive.writestr('index.json',canonical(index))
        if stream.tell()>5000000:raise Hold('Retained runtime source artifact exceeds its bound.')
        artifact=controller.store.artifact('runtime-source-delivery.zip',stream.getvalue())
        from . import repair
        if repair.blocked_progress(workspace):
            path=Path(workspace)/'progress.json';raw=path.read_bytes()
            controller.store.artifact('blocked-before-runtime-source-delivery.json',raw)
            write_copy(Path(workspace),'previous-candidates/blocked-runtime-source-'+digest(raw)+'.json',raw)
            path.unlink()
            guard=controller.state.get('repair_guard')
            if guard:guard.update(waiting=False,unchanged_slices=0)
        controller.state['runtime_source_delivery']=dict(binding=binding,infrastructure_revision=reference['revision'],artifact_id=artifact['id'])
        controller.state['feedback']='Verified runtime source references are available in references/runtime-source/index.json. Read those files before requesting source-delivery capabilities. Earlier blockers remain in private artifacts; limits and uncertain-call identities are unchanged. '+controller.state.get('feedback','')
        controller.store.event('runtime-source-delivered','Verified installed broker files and scoped infrastructure source supplied to the coding workspace.',dict(binding=binding,artifact_id=artifact['id'],installed_revision=observation['source_revision'],infrastructure_revision=reference['revision']))
        controller.save_state()
    # Recreate the index from trusted inputs; no candidate reference is ever
    # loaded as the installed broker or as a qualification certificate.
    target=root/'index.json'
    if target.is_symlink():raise Hold('Runtime source index cannot be a link.')
    save(target,canonical(index))
    return binding
