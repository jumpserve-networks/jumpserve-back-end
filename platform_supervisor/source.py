"""Freeze plain source files, preserving a baseline outside the coding sandbox."""
import base64
import io
import os
from pathlib import Path, PurePosixPath
import tarfile
import zipfile
from .common import POLICY, canonical, digest, Hold

def safe_name(name):
    path=PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or '`' in name or any(ord(char)<32 for char in name) or path.as_posix()!=name: raise Hold('Unsafe source path.')
    if any(part in POLICY['source_exclusions'] or part.startswith('.env') or part in ('.aws','.ssh') for part in path.parts): return False
    return True

def extract(raw, destination):
    total=0; names=set(); destination=Path(destination)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        for item in archive:
            parts=PurePosixPath(item.name).parts
            if len(parts)<2: continue
            name='/'.join(parts[1:])
            if not safe_name(name) or item.isdir(): continue
            if not item.isfile() or item.size>20000000 or name in names: raise Hold('Source archive contains a link, oversized file or duplicate path.')
            total+=item.size; names.add(name)
            if total>200000000 or len(names)>15000: raise Hold('Source archive exceeds the installed source limit.')
            path=destination/name; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(archive.extractfile(item).read()); path.chmod(0o755 if item.mode&0o111 else 0o644)

def snapshot(directory):
    directory=Path(directory); result={}; total=0
    if directory.is_symlink():raise Hold('Source root cannot be a symlink.')
    for root, directories, files in os.walk(directory,followlinks=False):
        if any((Path(root)/name).is_symlink() for name in directories):raise Hold('Candidate source contains a directory symlink.')
        directories[:]=sorted(d for d in directories if safe_name((Path(root)/d).relative_to(directory).as_posix()))
        for name in sorted(files):
            path=Path(root)/name; relative=path.relative_to(directory).as_posix()
            if not safe_name(relative): continue
            if path.is_symlink() or not path.is_file(): raise Hold('Candidate source contains a link or special file.')
            raw=path.read_bytes(); total+=len(raw)
            if len(raw)>20000000 or total>200000000 or len(result)>15000: raise Hold('Source snapshot exceeds its bound.')
            result[relative]=dict(sha256=digest(raw),mode='100755' if path.stat().st_mode&0o111 else '100644')
    return result

def protected(name, existed):
    if any(name==pattern or name.startswith(pattern) for pattern in POLICY['protected']): return True
    return existed and any(part in ('test','tests','__tests__') for part in PurePosixPath(name).parts)

def freeze(baseline, directory):
    current=snapshot(directory); changes=[]; total=0
    for name in sorted(set(baseline)|set(current)):
        if baseline.get(name)==current.get(name): continue
        if protected(name,name in baseline): raise Hold('Automatic publication cannot change authority, migrations, CI or existing validation: '+name)
        raw=(Path(directory)/name).read_bytes() if name in current else None
        if raw is not None:
            total+=len(raw)
            # A secret-looking candidate is retained for review, never published.
            for marker in (b'-----BEGIN PRIVATE KEY', b'AKIA', b'ghp_', b'github_pat_', b'sb_secret_'):
                if marker in raw: raise Hold('Candidate requires a private credential review: '+name)
        changes.append(dict(path=name,mode=current[name]['mode'] if name in current else baseline[name]['mode'],content=base64.b64encode(raw).decode() if raw is not None else None))
    if not changes: return None
    if total>POLICY['maximum_patch_bytes'] or len(changes)>POLICY['maximum_changed_files']: raise Hold('Candidate exceeds automatic patch limits.')
    return dict(version='platform-patch-v1',tree_sha256=digest(canonical(current)),files=changes)

def apply_patch(patch, directory):
    for item in patch['files']:
        name=item['path']
        if not safe_name(name) or protected(name,False): raise Hold('Unsafe retained patch.')
        path=Path(directory)/name
        if item['content'] is None: path.unlink()
        else:
            path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(base64.b64decode(item['content'],validate=True));path.chmod(0o755 if item['mode']=='100755' else 0o644)

def checkpoint(files_by_repository, metadata=None):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for repository,patch in files_by_repository.items(): archive.writestr(repository.split('/')[1]+'.json',canonical(patch))
        archive.writestr('work-packet.json',canonical(metadata or {}))
    if stream.tell()>5000000: raise Hold('Working checkpoint exceeds its private artifact limit.')
    return stream.getvalue()
