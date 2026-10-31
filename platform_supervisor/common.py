import hashlib
import json
import os
import ssl
import urllib.request
from pathlib import Path
import uuid

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def identity(task, label):
    return str(uuid.uuid5(uuid.UUID(task), label))

def network_opener(redirect_handler):
    context=ssl.create_default_context(cafile='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').exists() else None)
    context.keylog_filename=None
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),redirect_handler)

def save(path, raw):
    """Atomic, fsynced local receipt; secrets are never written by this helper."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending-' + str(uuid.uuid4()))
    with temporary.open('xb') as file:
        os.chmod(temporary, 0o600)
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    with _directory(path.parent) as fd:
        os.fsync(fd)

class _directory:
    def __init__(self, path): self.path = path
    def __enter__(self): self.fd = os.open(self.path, os.O_RDONLY); return self.fd
    def __exit__(self, *args): os.close(self.fd)

POLICY_BYTES = Path(__file__).with_name('policy.json').read_bytes()
POLICY = json.loads(POLICY_BYTES)
POLICY_SHA256 = digest(POLICY_BYTES)

class Hold(ValueError):
    """A recorded prerequisite or uncertain action, never an inferred success."""
    def __init__(self, reason, status='blocked'):
        super().__init__(reason)
        self.status = status
