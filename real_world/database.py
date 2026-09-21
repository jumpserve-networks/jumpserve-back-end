"""Small server-only Supabase HTTP adapter; no credentials go to EC2 or browsers."""
from functools import lru_cache
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request


class StorageMissing(Exception):
    pass


@lru_cache(maxsize=1)
def service_key():
    import cloud
    return cloud.client("secretsmanager", "us-east-1").get_secret_value(
        SecretId=os.environ["SUPABASE_SECRET_ARN"])["SecretString"]


def base_url():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("SUPABASE_URL must use HTTPS")
    return url


def request(path, method="GET", body=None, headers=None, raw=False, max_bytes=64 * 1024 * 1024):
    key = service_key()
    payload = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode() if body is not None else None
    request_headers = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base_url() + path, data=payload, method=method, headers=request_headers)
    ca_file = os.environ.get("SSL_CERT_FILE") or ("/etc/ssl/cert.pem" if os.path.isfile("/etc/ssl/cert.pem") else None)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context(cafile=ca_file)) as response:
            if int(response.headers.get("Content-Length", "0")) > max_bytes:
                raise ValueError("Artifact exceeds interactive analysis limits; use the raw download.")
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("Artifact exceeds interactive analysis limits; use the raw download.")
            return data if raw else json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        # Never log an authenticated request, signed URL, or returned SQL detail.
        try:
            detail = json.loads(error.read(4096))
        except (ValueError, UnicodeDecodeError):
            detail = {}
        if path.startswith("/storage/v1/") and (error.code == 404 or detail.get("code") in ("NoSuchKey", "NoSuchObject")
                or str(detail.get("statusCode")) == "404"):
            raise StorageMissing() from None
        raise RuntimeError(f"Supabase request failed (HTTP {error.code}).") from None


def rest(table, method="GET", body=None, params=None, headers=None):
    suffix = "?" + urllib.parse.urlencode(params) if params else ""
    return request("/rest/v1/" + table + suffix, method, body, headers)


def rpc(name, payload):
    return rest("rpc/" + name, "POST", payload)
