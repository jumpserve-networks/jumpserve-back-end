"""Import the original IPv6 claim/source register without upgrading its evidence."""
import json
import uuid
from pathlib import Path
from workflow import digest, paper_details, validate_record

TYPE_BY_ID = {"folded-cells":"numerical", "rq1-timeouts":"numerical", "rq2-mtu":"numerical", "rq3-fragments":"numerical", "rq4-causality":"causal", "rq5-dnssec":"association", "readiness-weighting":"generalization", "rfc3901":"standard", "analysis-defects":"algorithm", "rfc7872":"generalization", "apnic":"generalization", "packet-sizes":"numerical", "route-validation":"operational", "policy-recommendation":"operational", "relative-figures":"numerical"}

def ipv6(path, study_id):
    raw = Path(path).read_bytes()
    if len(raw)>100_000_000: raise ValueError("Legacy assessment exceeds the declared 100 MB import limit.")
    original = json.loads(raw); sha = digest(raw); namespace = uuid.UUID(study_id)
    def record_id(label): return str(uuid.uuid5(namespace, label))
    entries = []
    main = next(s for s in original["sources"] if s["reference_number"] == 0)
    paper = paper_details(dict(title="How I learned to stop worrying and love IPv6", paper_url=main["source_url"], domain="DNS over IPv6", scope="Imported historical claim/source register only. Original scientific data remain in the IPv6 DNS Study; no new measurements or claim upgrades.", origin_module="ipv6-dns-study"))
    for source in original["sources"]:
        number = source["reference_number"]
        review = source["review_status"]
        row = dict(id=record_id(f"source:{number}"), citation=source["citation"], source_url=source.get("source_url"), retrieved_url=source.get("retrieved_url"), kind=source["kind"], role=source["role"], retrieved_version=source.get("retrieved_version"), sha256=source.get("sha256"), byte_count=source.get("byte_count"), access_status="imported:"+source["access_status"], review_status=review, review_definition=original["review_definition"], examined=source.get("findings") or "No substantive examination recorded in the original.", unexamined="none" if review=="complete-review" else source.get("limitations") or "Original review does not establish complete examination.", retrieval_attempts=source.get("retrieval_attempts", []), reviewer=source["reviewer"], findings=source.get("findings") or "No finding recorded in the original.", limitations=source["limitations"])
        entries.append(dict(kind="sources", record=validate_record("sources", row)))
    for claim in original["claims"]:
        key = claim["id"]; claim_id = record_id("claim:"+key); assessment_id = record_id("assessment:"+key)
        priority = 5 if key in ("rq4-causality", "readiness-weighting", "policy-recommendation") else 4 if key in ("rq5-dnssec", "relative-figures") else 3
        row = dict(id=claim_id, source_id=record_id("source:0"), location=claim["location"], description=claim["description"], claim_type=TYPE_BY_ID.get(key,"operational"), scope={"origin_claim_id":key,"origin_assessment_sha256":sha,"conditions_and_limits":claim["limitation"]}, metrics=[], priority=priority)
        entries.append(dict(kind="claims",record=validate_record("claims",row)))
        row = dict(id=assessment_id, claim_id=claim_id, campaign_id=None, supersedes_id=None, label=claim["assessment"], tested_conditions={"origin":"ipv6-dns-study; assessment-v2", "original_assessment_sha256":sha, "scope":"Original archived reanalysis / source review / finite code controls as described by the original claim. This import performs none of those experiments again."}, evidence=[{"origin_module":"ipv6-dns-study","claim_id":key,"description":claim["evidence"],"assessment_sha256":sha}], justification=claim["evidence"], limitations=claim["limitation"], reviewer=main["reviewer"])
        entries.append(dict(kind="assessments",record=validate_record("assessments",row)))
        if claim["assessment"] not in ("inconclusive","untested"): continue
        row = dict(id=record_id("gap:"+key),claim_id=claim_id,assessment_id=assessment_id,supersedes_id=None,status="open",reason=claim["limitation"],next_check=claim["proposed_check"],required_inputs=[claim["required_inputs"]],dependencies=[{"description":claim["required_inputs"],"status":"not re-audited by this import"}],feasibility=claim["feasibility"],estimated_cost_usd=None,cost_basis="Not estimated. Missing cost is not zero.",decision_rule="Before execution, freeze a method appropriate to this claim and its comparison criterion. This imported proposal is not a completed test or a frozen main campaign.",stopping_rule="Stop if the exact required inputs or operational authorization are unavailable; preserve the gap. Do not substitute present-day observations for historical conditions.",priority=priority,campaign_id=None)
        entries.append(dict(kind="gaps",record=validate_record("gaps",row)))
    return dict(study_id=study_id,paper=paper,records=entries,provenance=dict(transformation="research_workflow/bridge.py; register-only import",original_sha256=sha,original_bytes=len(raw),legacy_measurements="Not copied or reanalyzed; original ipv6_study_* relations and private artifacts remain unchanged."))
