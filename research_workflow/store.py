"""Server-only persistence with exact target and artifact round-trip checks."""
from functools import lru_cache
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from workflow import FIELDS, PUBLIC_KINDS, canonical, digest, identifier, now, validate_record

PROJECT = "regphejnlvfpyokpniny"
ACCOUNT = "395567831870"
BUCKET = "research-workflow-raw"

class StoreError(Exception):
    pass

@lru_cache(maxsize=1)
def service_key():
    if os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        return os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    import boto3
    arn = os.environ["SUPABASE_SECRET_ARN"]
    if arn.split(":")[4] != ACCOUNT: raise StoreError("AWS secret account mismatch.")
    return boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(SecretId=arn)["SecretString"]

def request(path, method="GET", body=None, public=False, raw=False):
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if base != f"https://{PROJECT}.supabase.co": raise StoreError("JumpServe Supabase project mismatch.")
    key = os.environ["SUPABASE_ANON_KEY"] if public else service_key()
    payload = body if isinstance(body, bytes) else canonical(body) if body is not None else None
    headers = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/octet-stream" if isinstance(body, bytes) else "application/json"}
    req = urllib.request.Request(base + path, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = response.read(20_000_001)
            if len(data) > 20_000_000: raise StoreError("Persistence response exceeds the bounded size.")
            return data if raw else json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        # No signed URLs, credentials, owner identifiers or SQL details in errors.
        raise StoreError(f"Research persistence request failed (HTTP {error.code}); preserve original inputs and retry using the same record ID. Storage is not confirmed.") from None

def rows(kind, params=None, public=False):
    if public and kind not in (*PUBLIC_KINDS, "studies"): raise ValueError("Private relations cannot be publicly read.")
    table = "research_" + kind
    if kind in FIELDS:
        projection = ",".join(["id", "study_id", "created_at", *FIELDS[kind]])
    elif kind == "studies":
        projection = "id,title,paper_url,domain,scope,origin_module,created_at"
    elif kind == "study_owners" and not public:
        projection = "study_id,owner_id"
    elif kind == "queue_jobs" and not public:
        projection = "id,study_id,campaign_id,input_artifact_id,followup_of,execution_mode,priority,dependencies,exclusive_resources,request,definition_sha256,status,reason,run_id,lease_until,created_at,updated_at"
    elif kind == "queue_events" and not public:
        projection = "id,study_id,job_id,event,status,reason,details,created_at"
    else:
        raise ValueError("Unsupported research relation.")
    query = {"select": projection, "order": "created_at.asc,id.asc", "limit": 1000, **(params or {})}
    if kind == "study_owners": query.pop("order", None)
    result = request("/rest/v1/" + table + "?" + urllib.parse.urlencode(query), public=public)
    if kind in ('measurements', 'published_values'):
        for row in result:
            if row.get('value') is not None: row['value'] = str(row['value'])
    return result

def rpc(name, payload):
    if name not in ("research_create_study", "research_append", "research_append_bundle", "research_publish", "research_queue_enqueue", "research_queue_claim", "research_queue_finish", "research_queue_action", "research_queue_snapshot"): raise ValueError("Unsupported research operation.")
    return request("/rest/v1/rpc/" + name, "POST", payload)

def owns(study_id, actor):
    return bool(rows("study_owners", {"study_id": "eq." + study_id, "owner_id": "eq." + actor, "limit": 1}))

def append(study_id, actor, kind, record):
    return rpc("research_append", dict(p_study=study_id, p_actor=actor, p_kind=kind, p_record=validate_record(kind, record)))

def append_bundle(study_id, actor, entries):
    validated = [dict(kind=row["kind"], record=validate_record(row["kind"], row["record"])) for row in entries]
    return rpc("research_append_bundle", dict(p_study=study_id, p_actor=actor, p_records=validated))

def artifact(study_id, actor, raw, record_id, media_type, provenance):
    sha = digest(raw); path = study_id + "/" + sha
    endpoint = "/storage/v1/object/" + BUCKET + "/" + path
    try:
        prior = request(endpoint, raw=True)
    except StoreError as error:
        if "HTTP 400" not in str(error) and "HTTP 404" not in str(error): raise
        request(endpoint, "POST", raw)
        prior = request(endpoint, raw=True)
    if digest(prior) != sha or prior != raw: raise StoreError("Stored original-byte artifact hash differs after retrieval.")
    record = dict(id=identifier(record_id), sha256=sha, byte_count=len(raw), storage_path=path, media_type=media_type,
        provenance=provenance, verified_at=now())
    existing = rows('artifacts', {'id':'eq.'+record['id'], 'study_id':'eq.'+study_id, 'limit':1})
    if existing:
        prior_record = {key:existing[0][key] for key in ('id', *FIELDS['artifacts'])}
        if any(prior_record[key] != record[key] for key in record if key != 'verified_at'):
            raise StoreError('Artifact record ID already identifies different bytes or provenance; preserve the original and create a version.')
        return prior_record
    # Artifact bytes remain private even when study results are published.
    return record

def artifact_bytes(study_id, artifact_id, maximum=256000):
    records = rows("artifacts", {"study_id": "eq." + identifier(study_id), "id": "eq." + identifier(artifact_id), "limit": 1})
    if not records: raise StoreError("Original input artifact is unavailable.")
    record = records[0]
    if record["byte_count"] > maximum or record["storage_path"] != study_id + "/" + record["sha256"]:
        raise StoreError("Original input artifact exceeds the bound or has an invalid content identity.")
    raw = request("/storage/v1/object/" + BUCKET + "/" + record["storage_path"], raw=True)
    if len(raw) != record["byte_count"] or len(raw) > maximum or digest(raw) != record["sha256"]:
        raise StoreError("Original input artifact hash differs after retrieval.")
    return raw

def snapshot(study_id, owner=False, maximum=10000, byte_budget=4_000_000):
    studies = rows("studies", {"id": "eq." + study_id, "limit": 1}, public=not owner)
    if not studies: return None
    boundary = now()
    current = rows("publications", {"study_id": "eq." + study_id, "order": "version.desc", "limit": 1}, public=not owner) if not owner else []
    if not owner and (not current or current[0]["status"] != "published"): return None
    data = {"study": studies[0], "access": "owner-draft" if owner else "published-snapshot", "records": {}, "coverage": {}}
    used = len(canonical(data))
    for kind in PUBLIC_KINDS:
        collected = []
        for offset in range(0, maximum + 1, 1000):
            page = rows(kind, {"study_id": "eq." + study_id, "created_at": "lte." + boundary, "offset": offset}, public=not owner)
            used += len(canonical(page))
            if used > byte_budget: raise StoreError("Study export exceeds the interactive byte budget; use an operator export. Data were not truncated or zero-filled.")
            collected.extend(page)
            if len(collected) > maximum: raise StoreError("Study export exceeds interactive coverage limits; use the operator export. No truncated dataset is presented as complete.")
            if len(page) < 1000: break
        data["records"][kind] = collected
        data["coverage"][kind] = len(collected)
        if not owner and kind != "publications" and set(row["id"] for row in collected) != set(current[0]["record_ids"].get(kind, [])):
            raise StoreError("Published snapshot coverage changed during retrieval. Retry; no mixed-version result is returned.")
    if not owner:
        latest = rows("publications", {"study_id": "eq." + study_id, "order": "version.desc", "limit": 1}, public=True)
        if not latest or latest[0]["id"] != current[0]["id"]: raise StoreError("Publication changed during retrieval; retry without mixing versions.")
    return data
