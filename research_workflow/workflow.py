"""Domain-aware record validation. Nothing in a paper is treated as instructions."""
import copy
import datetime as dt
import hashlib
import json
import math
import re
import uuid
from urllib.parse import urlsplit, parse_qsl

VERSION = "research-workflow-v1"
LABELS = {
    "reproduced": "The declared check meets its criterion under the recorded conditions; broader validity is not implied.",
    "discrepant": "Examined evidence conflicts with the claim or declared criterion; comparability limits remain explicit.",
    "inconclusive": "Relevant evidence was examined but cannot resolve the claim within the declared scope.",
    "untested": "No adequate check has been completed; a plan or citation is not a test.",
}
CLAIM_TYPES = ("numerical", "algorithm", "association", "causal", "generalization", "operational", "theory", "standard")
EXPERIMENT_TYPES = ("reanalysis", "author-implementation", "independent-check", "new-measurement", "broader-validation", "source-review")
PUBLIC_KINDS = ("sources", "claims", "protocols", "configurations", "campaigns", "claim_checks", "runs", "published_values", "measurements", "summaries", "assessments", "gaps", "reviews", "publications")

# Explicit field inventories double as the API's public projection allowlist.
FIELDS = {
 "sources": "citation source_url retrieved_url kind role retrieved_version sha256 byte_count access_status review_status review_definition examined unexamined retrieval_attempts reviewer findings limitations",
 "claims": "source_id location description claim_type scope metrics priority",
 "protocols": "version supersedes_id document sha256 frozen_at registration_kind amendment_reason",
 "configurations": "identity details input_versions requested_resources",
 "campaigns": "protocol_id followup_of title stage experiment_type adapter planned_units coverage_limits",
 "claim_checks": "claim_id campaign_id method applicability rationale",
 "runs": "campaign_id configuration_id status reason started_at ended_at analysis_version input_sha256 output_sha256 requested_resources actual_resources usage provenance",
 "published_values": "source_id location observation_id configuration_identity metric units value status reason extraction",
 "measurements": "run_id published_id observation_id configuration_identity metric units value status reason details",
 "summaries": "run_id metric units statistics interval_kind uncertainty_method planned unexpected recorded missing invalid ambiguous excluded",
 "assessments": "claim_id campaign_id supersedes_id label tested_conditions evidence justification limitations reviewer",
 "gaps": "claim_id assessment_id supersedes_id status reason next_check required_inputs dependencies feasibility estimated_cost_usd cost_basis decision_rule stopping_rule priority campaign_id",
 "reviews": "scope status reviewer judgments limitations",
 "artifacts": "sha256 byte_count storage_path media_type provenance verified_at",
 "publications": "version status scientific_review_id software_review_id record_ids reason",
}
FIELDS = {kind: names.split() for kind, names in FIELDS.items()}
NULLABLE = {"source_url", "retrieved_url", "retrieved_version", "sha256", "byte_count", "supersedes_id", "amendment_reason", "followup_of", "configuration_id", "reason", "input_sha256", "output_sha256", "published_id", "value", "campaign_id", "assessment_id", "estimated_cost_usd"}
JSON_FIELDS = {"retrieval_attempts", "reviewer", "scope", "metrics", "document", "details", "input_versions", "requested_resources", "actual_resources", "usage", "provenance", "statistics", "tested_conditions", "evidence", "required_inputs", "dependencies", "judgments"}
INTEGER_FIELDS = {"version", "priority", "byte_count", "planned_units", "planned", "unexpected", "recorded", "missing", "invalid", "ambiguous", "excluded"}

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A UUID identifier is required.") from error

def text(value, name, maximum=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text, at most {maximum} characters.")
    return value

def finite(value, name):
    try: valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError: valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite JSON number; missing is null, not zero.")
    return value

def public_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source URLs must identify an HTTP(S) resource without embedded credentials.")
    if any(key.lower() in ("token", "access_token", "apikey", "api_key", "authorization", "signature") or key.lower().startswith("x-amz-") for key, _ in parse_qsl(parsed.query)):
        raise ValueError("Credential-bearing URLs cannot be stored in public source metadata.")
    return value

def paper_details(body):
    allowed = {"title", "paper_url", "domain", "scope", "origin_module"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError("Unsupported paper details.")
    result = {name: text(body.get(name), name, 300 if name == "title" else 5000) for name in ("title", "paper_url", "domain", "scope")}
    if not re.fullmatch(r"https://[^\s]+", result["paper_url"]):
        raise ValueError("Use an HTTPS paper URL or DOI URL. Intake records the URL; it does not fetch arbitrary browser-supplied URLs.")
    public_url(result["paper_url"])
    result["origin_module"] = body.get("origin_module")
    if result["origin_module"] is not None:
        text(result["origin_module"], "origin_module", 100)
    return result

def reviewer(value):
    if not isinstance(value, dict) or value.get("type") not in ("human", "AI"):
        raise ValueError("Record whether the reviewer is human or AI.")
    for key in ("identity", "independence"):
        text(value.get(key), "reviewer." + key, 300)
    if "@" in value["identity"]:
        raise ValueError("Use a public reviewer label, not an email address. Actor identity is recorded privately.")
    return value

def validate_protocol(document):
    required = ("questions", "hypotheses", "metrics", "configurations", "input_versions", "dates_or_epochs", "algorithms", "seeds", "controls", "sensitivity", "replication_unit", "sample_size_rationale", "exclusions", "missing_data", "statistics", "dependence", "resource_limits", "stopping_rule", "prior_exposure", "coverage_limits")
    if not isinstance(document, dict) or any(key not in document for key in required):
        raise ValueError("Protocol is incomplete: " + ", ".join(key for key in required if not isinstance(document, dict) or key not in document))
    canonical(document)
    if document.get('data_origin','unclassified') not in ('archived-data','new-empirical','simulation','model-output','software-fixture','mixed','unclassified'):
        raise ValueError('Unknown data_origin; classify archived, empirical, simulation, model or software fixture inputs explicitly.')
    for key in ("replication_unit", "sample_size_rationale", "exclusions", "missing_data", "statistics", "dependence", "stopping_rule", "prior_exposure", "coverage_limits"):
        text(document[key], key)
    if document['prior_exposure'].startswith('Template must be amended'):
        raise ValueError('Disclose actual prior exposure before freezing the protocol; the template placeholder is not a registration.')
    for key in ("questions", "metrics", "configurations", "input_versions"):
        if not isinstance(document[key], list) or not document[key]:
            raise ValueError(key + " must be a nonempty array.")
    for question in document["questions"]: text(question, "question")
    text(document["hypotheses"], "hypotheses")
    text(document["dates_or_epochs"], "dates_or_epochs")
    for key in ("algorithms", "controls"):
        if not isinstance(document[key], list) or not document[key]: raise ValueError(key + " must contain declarations or explicit inapplicability with rationale.")
        for item in document[key]: text(item, key)
    configurations = set()
    for configuration in document["configurations"]:
        if not isinstance(configuration, dict) or not isinstance(configuration.get("details"), dict): raise ValueError("Configurations need exact identities and recorded details.")
        identity = text(configuration.get("identity"), "configuration identity", 300)
        if identity in configurations: raise ValueError("Duplicate configuration identity.")
        configurations.add(identity)
    metric_ids = set()
    for metric in document["metrics"]:
        if not isinstance(metric, dict): raise ValueError("Metric declarations must be objects.")
        name = text(metric.get("name"), "metric name", 100)
        text(metric.get("units"), "metric units", 100)
        if name in metric_ids: raise ValueError("Duplicate metric declaration.")
        metric_ids.add(name)
        if "tolerance_absolute" in metric and finite(metric["tolerance_absolute"], "absolute tolerance") < 0:
            raise ValueError("Tolerance cannot be negative.")
        for bound in ("lower_bound", "upper_bound"):
            if bound in metric: finite(metric[bound], bound)
        if "lower_bound" in metric and "upper_bound" in metric and metric["lower_bound"] > metric["upper_bound"]:
            raise ValueError("Metric bounds are reversed.")
    for value in document["input_versions"]:
        if not isinstance(value, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("sha256", ""))):
            raise ValueError("Each protocol input needs its exact original-byte SHA256.")
        text(value.get("identity"), "input identity", 300)
    limits = document["resource_limits"]
    if not isinstance(limits, dict): raise ValueError("Resource limits must be explicit.")
    for key, bound in (("max_observations", 10000), ("max_input_bytes", 10_000_000), ("wall_seconds", 300)):
        value = limits.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= bound:
            raise ValueError(f"{key} must be an integer from 1 through {bound}.")
    # Applicability is expressed, rather than inventing seeds or independence.
    for key in ("seeds", "sensitivity"):
        entry = document[key]
        if not isinstance(entry, dict) or not isinstance(entry.get("applicable"), bool) or not isinstance(entry.get("rationale"), str) or not entry["rationale"].strip():
            raise ValueError(key + " needs an applicability flag and rationale.")
    return document

def validate_record(kind, body):
    if kind not in FIELDS or kind == "publications" or not isinstance(body, dict):
        raise ValueError("Unsupported record kind.")
    fields = FIELDS[kind]
    if set(body) - set(fields) - {"id"}:
        raise ValueError("Unsupported fields: " + ", ".join(sorted(set(body) - set(fields) - {"id"})))
    result = {"id": identifier(body.get("id"))}
    for name in fields:
        value = copy.deepcopy(body.get(name))
        if value is None and name not in NULLABLE:
            raise ValueError(name + " is required.")
        if value is not None:
            if name.endswith("_id") and name != "observation_id":
                value = identifier(value)
            elif name in JSON_FIELDS:
                if not isinstance(value, (dict, list)): raise ValueError(name + " must be JSON data.")
                canonical(value)
            elif name in INTEGER_FIELDS:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0: raise ValueError(name + " must be a nonnegative integer.")
            elif name in ("value", "estimated_cost_usd"):
                finite(value, name)
            else:
                text(value, name)
        result[name] = value
    if "reviewer" in result: reviewer(result["reviewer"])
    for key in ("sha256", "input_sha256", "output_sha256"):
        if result.get(key) is not None and not re.fullmatch(r"[a-f0-9]{64}", result[key]):
            raise ValueError(key + " is not a SHA256 digest.")
    if "priority" in result and not 1 <= result["priority"] <= 5: raise ValueError("Priority must be 1 through 5.")
    if kind == "claims" and result["claim_type"] not in CLAIM_TYPES: raise ValueError("Unknown claim type.")
    if kind == "protocols":
        validate_protocol(result["document"])
        if result["sha256"] != digest(canonical(result["document"])): raise ValueError("Protocol hash does not match its canonical document.")
        if result["registration_kind"] not in ("prospective", "retrospective-import"): raise ValueError("Unknown registration kind.")
    if kind == "sources":
        for field in ("source_url", "retrieved_url"):
            if result[field] is not None: public_url(result[field])
        if result["review_status"] not in ("complete-review", "partial-review", "retrieved-unreviewed", "unavailable-full-text"): raise ValueError("Unknown review status.")
        if result["review_status"] == "complete-review" and result["unexamined"].strip().lower() not in ("none", "none under the stated review definition"):
            raise ValueError("Complete review cannot leave unexamined material.")
        if result["access_status"] == "retrieved" and (result["sha256"] is None or result["byte_count"] is None): raise ValueError("Retrieved source requires original-byte identity.")
    if kind == "campaigns" and (result["experiment_type"] not in EXPERIMENT_TYPES or result["stage"] not in ("pilot", "main", "follow-up", "retrospective-import")):
        raise ValueError("Unknown experiment type or campaign stage.")
    if kind in ("measurements", "published_values"):
        statuses = ("recorded", "missing", "invalid", "ambiguous") + (("excluded",) if kind == "measurements" else ())
        if result["status"] not in statuses: raise ValueError("Unknown observation status.")
        if result["status"] == "recorded" and result["value"] is None: raise ValueError("Recorded observation needs a value; zero is valid.")
        if result["status"] != "recorded" and (result["value"] is not None or not result["reason"]): raise ValueError("Non-recorded observations have null values and explicit reasons.")
    if kind == "assessments":
        if result["label"] not in LABELS: raise ValueError("Unknown assessment label.")
        if not isinstance(result["evidence"], list) or (result["label"] in ("reproduced", "discrepant") and not result["evidence"]): raise ValueError("A supported or discrepant finding needs evidence references.")
    if kind == "gaps":
        if result["status"] not in ("open", "blocked", "planned", "resolved", "stopped", "inapplicable"): raise ValueError("Unknown gap status.")
        if result["estimated_cost_usd"] is not None and result["estimated_cost_usd"] < 0: raise ValueError("Estimated cost cannot be negative.")
    if kind == "runs":
        if result["status"] not in ("complete", "partial", "failed", "excluded", "invalid") or (result["status"] != "complete" and not result["reason"]): raise ValueError("Run status requires a reason unless complete.")
        try:
            if dt.datetime.fromisoformat(result["ended_at"]) < dt.datetime.fromisoformat(result["started_at"]): raise ValueError("Run timestamps are reversed.")
        except TypeError as error: raise ValueError("Run timestamps require equivalent time zones.") from error
    if kind == "summaries":
        if sum(result[key] for key in ("recorded", "missing", "invalid", "ambiguous", "excluded")) != result["planned"] + result["unexpected"]: raise ValueError("Summary coverage does not conserve planned and unexpected observations.")
        if result["interval_kind"] not in ("none", "descriptive", "confidence", "unsupported"): raise ValueError("Unknown interval kind.")
    return result

def protocol_record(document, record_id, supersedes_id=None, version=1, amendment_reason=None, registration_kind="prospective"):
    validate_protocol(document)
    return validate_record("protocols", dict(id=record_id, version=version, supersedes_id=supersedes_id, document=document,
      sha256=digest(canonical(document)), frozen_at=now(), registration_kind=registration_kind, amendment_reason=amendment_reason))

def numerical_protocol(input_sha256, metric, units, tolerance, configurations, max_observations=1000):
    """A deterministic census template, not a randomized or generalization study."""
    return dict(questions=["Do every supplied matched numerical observation and separately sourced published value agree under the declared absolute tolerance?"],
      hypotheses="Numerical agreement is checked separately from correctness, causality and generalization.",
      metrics=[dict(name=metric, units=units, tolerance_absolute=tolerance)], configurations=configurations,
      input_versions=[dict(identity="matched-input.json", sha256=input_sha256)], dates_or_epochs="Use the explicit identities and epochs in the input; no present-day substitution.",
      algorithms=["matched-numeric-v1; exact Decimal absolute differences"], seeds=dict(applicable=False, rationale="Deterministic census; no randomized algorithm."),
      controls=["Exact observation/configuration/metric/units matching", "No zero imputation", "Unique observation keys"],
      sensitivity=dict(applicable=False, rationale="This campaign checks one fixed declared tolerance; amended tolerances require separate protocols."),
      replication_unit="The explicitly identified observation; no independence is inferred between observations.",
      sample_size_rationale="Census of the supplied published comparison cells, with unexpected observations preserved as exclusions.",
      exclusions="Unmatched observations are excluded and recorded; duplicates, unit mismatches and changed inputs fail the run.",
      missing_data="Absent expected observations remain missing. Invalid and ambiguous values remain distinct from recorded zero.",
      statistics="Descriptive exact comparisons only; no inferential intervals.", dependence="Dependencies may exist; no independent replications or population inference are asserted.",
      resource_limits=dict(max_observations=max_observations, max_input_bytes=2_000_000, wall_seconds=10, estimated_cost_usd=None, cost_basis="Local or API hosting costs are unallocated; missing charges are not zero. No experiment instances or model calls are requested."),
      stopping_rule="Stop on an input identity, schema, configuration, units or budget violation; preserve the failed attempt.",
      prior_exposure="Template must be amended to disclose actual prior inspection before freezing.",
      coverage_limits="Numerical agreement within supplied cells only; no correctness, compliance, causal, operational or population validation.")
