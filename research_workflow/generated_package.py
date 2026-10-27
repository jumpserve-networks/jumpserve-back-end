"""Freeze a builder project; never execute its code on the controller host.

The same module is the server-owned wrapper on the disposable experiment host.
The ZIP is an input, not an installer or a model-authored artifact identifier.
"""
import ast
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import zipfile

VERSION = 'research-built-package-v1'
MAX_ARCHIVE = 256000
MAX_EXPANDED = 1000000
MAX_FILES = 64

def members(raw):
    if len(raw) > MAX_ARCHIVE: raise ValueError('Built project exceeds 256 KB; retain a separately versioned input instead of truncating.')
    result = {}; total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for item in archive.infolist():
            name = item.filename; path = PurePosixPath(name)
            if not name or path.is_absolute() or '..' in path.parts or '\\' in name or str(path) != name or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                raise ValueError('Project ZIP must contain only unique regular relative files.')
            if name in result or len(result) >= MAX_FILES or item.file_size > 256000: raise ValueError('Project ZIP file bound or duplicate path rejected.')
            total += item.file_size
            if total > MAX_EXPANDED: raise ValueError('Project ZIP expanded size exceeds 1 MB.')
            result[name] = archive.read(item)
            try: result[name].decode('utf-8')
            except UnicodeDecodeError: raise ValueError('Project archives contain UTF-8 sources; retain binary data as separate versioned inputs.') from None
    if not {'experiment.py', 'requirements.lock', 'system-packages.lock.json', 'build-evidence.json'} <= result.keys():
        raise ValueError('Project needs experiment.py, dependency locks and build-evidence.json.')
    ast.parse(result['experiment.py'])
    # No editable URLs, alternate indexes, implicit transitive installs or shells.
    for line in result['requirements.lock'].decode().splitlines():
        if not line.strip() or line.startswith('#'): continue
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+!-]+(?: --hash=sha256:[a-f0-9]{64})+', line):
            raise ValueError('Each pip dependency needs an exact version and SHA256 hashes on one line.')
    packages = json.loads(result['system-packages.lock.json'])
    if not isinstance(packages, list) or len(packages) > 32 or any(not isinstance(p, str) or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*=[A-Za-z0-9:.+~_-]+', p) for p in packages):
        raise ValueError('System dependencies need explicit apt package=version locks.')
    evidence = json.loads(result['build-evidence.json'])
    if not isinstance(evidence, dict) or set(evidence) != {'commands', 'checks', 'limitations'} or not all(isinstance(evidence[k], list) for k in evidence):
        raise ValueError('Build evidence must identify commands, checks and limitations; model notes do not establish execution.')
    return result

def freeze(directory):
    root = Path(directory); stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(root.rglob('*')):
            if file.is_symlink(): raise ValueError('Project symlinks are not portable frozen inputs.')
            if not file.is_file(): continue
            name = file.relative_to(root).as_posix()
            if any(p in ('.git', '.venv', '__pycache__', 'node_modules') for p in PurePosixPath(name).parts):
                raise ValueError('Keep dependency installations outside the versioned project directory.')
            if file.stat().st_size > 256000: raise ValueError('Project file exceeds its retained byte limit.')
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.read_bytes())
            if stream.tell() > MAX_ARCHIVE: raise ValueError('Project archive exceeds its input transport bound.')
    raw = stream.getvalue(); members(raw); return raw

def run():
    """Only invoked as package.py on a disposable, credential-free machine."""
    root = Path.cwd() / 'built-project'; root.mkdir(mode=0o700)
    raw = (Path(os.environ['JUMPSERVE_INPUTS']) / 'project.zip').read_bytes()
    files = members(raw)
    for name, value in files.items():
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(value)
    packages = json.loads(files['system-packages.lock.json'])
    if packages:
        subprocess.run(['apt-get', 'update'], check=True, timeout=180)
        subprocess.run(['apt-get', 'install', '-y', '--no-install-recommends', *packages], check=True, timeout=240)
    python=Path(sys.executable)
    if files['requirements.lock'].strip():
        subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')], check=True, timeout=30)
        python=root/'.venv/bin/python'
        subprocess.run([str(python), '-m', 'pip', 'install', '--require-hashes', '--disable-pip-version-check', '-r', str(root / 'requirements.lock')], check=True, timeout=240)
        subprocess.run([str(python), '-m', 'pip', 'freeze', '--all'], check=True, timeout=30)
    print(json.dumps(dict(runtime_python=sys.version,locked_system_packages=packages,python_dependencies='hash-locked' if files['requirements.lock'].strip() else 'stdlib-only')),flush=True)
    os.chdir(root)
    os.execve(str(python), [str(python), str(root / 'experiment.py'), *sys.argv[1:]], dict(os.environ))

if __name__ == '__main__': run()
