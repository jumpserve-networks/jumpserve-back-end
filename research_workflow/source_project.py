"""Package UTF-8 sources while retaining derived Python cache bytes separately.

The builder container must be stopped before sealing its workspace. No submitted
program is executed here; dependency directories, symlinks and bounds still fail.
The executable wrapper and its original archive validation remain unchanged.
"""
from pathlib import Path
import tempfile
import generated_package
from workflow import canonical, digest

VERSION = 'source-project-sealing-v1'

def freeze(job, directory, retain):
    root = Path(directory)
    if root.is_symlink(): raise ValueError('Project root cannot be a symlink.')
    files = []; caches = []; total = 0
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise ValueError('Project symlinks are not portable frozen inputs.')
        if not path.is_file(): continue
        name = path.relative_to(root).as_posix(); size = path.stat().st_size
        if size > 256000 or len(files) + len(caches) >= generated_package.MAX_FILES:
            raise ValueError('Source/cache project file bound exceeded.')
        total += size
        if total > generated_package.MAX_EXPANDED: raise ValueError('Source/cache project expanded bound exceeded.')
        raw = path.read_bytes()
        if any(part in ('.git', '.venv', 'node_modules') for part in path.relative_to(root).parts):
            raise ValueError('Keep dependency installations outside the versioned project directory.')
        if '__pycache__' in path.relative_to(root).parts:
            if path.suffix != '.pyc': raise ValueError('Only derived .pyc files may be separated from a Python cache directory.')
            caches.append((name, raw))
        else: files.append((name, raw))
    with tempfile.TemporaryDirectory(prefix='jumpserve-source-project-') as tmp:
        stage = Path(tmp)
        for name, raw in files:
            target = stage / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        archive = generated_package.freeze(stage)
    if caches:
        excluded = [dict(path=name, bytes=len(raw), sha256=digest(raw), original=retain(job, 'python-cache-'+digest(raw)+'.bin', raw)) for name, raw in caches]
        manifest = canonical(dict(version=VERSION, package_sha256=digest(archive), source_files=[dict(path=name, bytes=len(raw), sha256=digest(raw)) for name, raw in files],
            excluded=excluded, transformation='Original project and cache files unchanged. Executable ZIP contains the listed source files only; compiled caches retained separately. No scientific inputs or criteria changed.'))
        retain(job, 'source-project-'+digest(manifest)+'.json', manifest)
    return archive
