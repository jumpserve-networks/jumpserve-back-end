"""Private Supabase Storage uploads and immutable, SHA-256-addressed evidence."""
import hashlib
import urllib.parse

from config import nodes_for
import database

BUCKET = "real-world-results"
MAX_BYTES = 32 * 1024 * 1024


def object_path(key):
    return BUCKET + "/" + urllib.parse.quote(key, safe="/")


def signed_upload(job_id, name):
    result = database.request("/storage/v1/object/upload/sign/" + object_path(f"{job_id}/{name}.json"),
                              "POST", {}, {"x-upsert": "true"})
    # The upload capability is scoped to one object; it carries no service key.
    return database.base_url() + "/storage/v1" + result["url"]


def records(job_id):
    return database.rest("real_world_artifacts", params={"job_id": "eq." + job_id, "select": "*"})


def read(job_id, name, sources=None, max_bytes=MAX_BYTES):
    sources = records(job_id) if sources is None else sources
    source = next((r for r in sources if r["node_name"] == name), None)
    key = source["object_path"] if source else f"{job_id}/{name}.json"
    try:
        payload = database.request("/storage/v1/object/" + object_path(key), raw=True, max_bytes=max_bytes)
    except database.StorageMissing:
        if source:
            raise RuntimeError("Archived measurement object is missing.") from None
        return None
    digest = hashlib.sha256(payload).hexdigest()
    if source and (digest != source["sha256"] or len(payload) != source["bytes"]):
        raise RuntimeError("Measurement checksum differs from its archived source.")
    return payload, {"name": name + ".json", "sha256": digest, "bytes": len(payload),
                     "version_id": source.get("legacy_version_id") if source else None}


def archive(job_id, name, payload, legacy_version_id=None):
    digest = hashlib.sha256(payload).hexdigest()
    key = f"{job_id}/archive/{name}/{digest}.json"
    # A retry can only write the same bytes at this content-addressed path.
    database.request("/storage/v1/object/" + object_path(key), "POST", payload, {"x-upsert": "true"})
    database.rest("real_world_artifacts", "POST", {"job_id": job_id, "node_name": name,
        "object_path": key, "sha256": digest, "bytes": len(payload), "legacy_version_id": legacy_version_id},
        headers={"Prefer": "resolution=merge-duplicates"})


def freeze(job):
    sources = records(job["job_id"])
    for node in nodes_for(job["config"]):
        if any(r["node_name"] == node["name"] for r in sources):
            continue
        result = read(job["job_id"], node["name"], sources)
        if result:
            archive(job["job_id"], node["name"], result[0])


def downloads(job):
    sources = records(job["job_id"])
    # Listing is bounded to the exact test folder. Never sign arbitrary keys.
    staged = database.request("/storage/v1/object/list/" + BUCKET, "POST",
        {"prefix": job["job_id"], "limit": 100, "offset": 0})
    names = {entry["name"] for entry in staged if entry.get("id")}
    result = []
    for node in nodes_for(job["config"]):
        name = node["name"]
        source = next((r for r in sources if r["node_name"] == name), None)
        if source or name + ".json" in names:
            key = source["object_path"] if source else f'{job["job_id"]}/{name}.json'
            signed = database.request("/storage/v1/object/sign/" + object_path(key), "POST", {"expiresIn": 300})
            result.append({"name": name + ".json", "url": database.base_url() + "/storage/v1" + signed["signedURL"]})
    return {"artifacts": result}
