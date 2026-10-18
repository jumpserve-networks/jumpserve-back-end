"""Unpublished proposals: versioned planning is separate from empirical evidence."""
import json
import uuid
import store
import network_spec
from workflow import canonical, digest, identifier, now, protocol_record, text, validate_protocol, validate_record

VERSION = "research-ideas-v1"
FIELDS = ("problem", "question", "hypothesis", "conditions", "related_work", "alternatives", "design", "metrics", "controls", "falsification", "inputs", "limitations")
MAX_PACKET_BYTES = 24000

def document(value):
    if not isinstance(value, dict) or set(value) != {*FIELDS, "protocol"}:
        raise ValueError("Idea packets need the declared planning fields and a candidate protocol, which may be null while drafting.")
    for field in FIELDS:
        if not isinstance(value[field], str) or len(value[field]) > 5000:
            raise ValueError(field + " must be bounded text; leave genuinely unknown draft information empty.")
    if value["protocol"] is not None and not isinstance(value["protocol"], dict):
        raise ValueError("Candidate protocol must be a JSON object or null.")
    if len(canonical(value)) > MAX_PACKET_BYTES: raise ValueError("Idea packet exceeds the 24 KB planning budget.")
    return value

def completeness(value):
    missing = [field for field in FIELDS if not value[field].strip()]
    try: validate_protocol(value["protocol"])
    except (ValueError, TypeError, KeyError) as error: missing.append("Candidate protocol: " + str(error))
    units = value["protocol"].get("planned_units") if isinstance(value["protocol"], dict) else None
    if isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= 10000:
        missing.append("Candidate protocol: declare planned_units from 1 through 10000")
    return missing

def packet(study_id, packet_id):
    rows = store.rows("idea_packets", {"study_id": "eq." + study_id, "id": "eq." + identifier(packet_id), "limit": 1})
    if not rows: raise ValueError("Idea packet is unavailable in this study.")
    value = rows[0]
    raw = value["raw_document"].encode()
    if digest(raw) != value["sha256"] or json.loads(raw) != value["document"]:
        raise store.StoreError("Idea packet bytes differ from their stored hash or parsed document.")
    document(value["document"])
    return value

def status(study_id):
    packets = store.rows("idea_packets", {"study_id": "eq." + study_id, "order": "version.asc", "limit": 101})
    runs = store.rows("idea_runs", {"study_id": "eq." + study_id, "limit": 201})
    decisions = store.rows("idea_readiness", {"study_id": "eq." + study_id, "limit": 101})
    campaigns = store.rows("idea_campaign_links", {"study_id": "eq." + study_id, "limit": 101})
    if len(packets) > 100 or len(runs) > 200 or len(decisions) > 100 or len(campaigns) > 100:
        raise store.StoreError("Idea history exceeds the interactive budget; request a full operator export rather than truncated coverage.")
    current = packets[-1] if packets else None
    latest = next((row for row in reversed(decisions) if current and row["packet_id"] == current["id"]), None)
    latest_runs = {task: next((run for run in reversed(runs) if current and run["packet_id"] == current["id"] and run["task"] == task), None) for task in ("comprehension", "critic")}
    ready = bool(latest and latest["decision"] == "ready" and all(latest_runs[task] and latest_runs[task]["id"] == latest[task + "_run_id"] and latest_runs[task]["status"] == "complete" for task in latest_runs))
    return dict(version=VERSION, packets=packets, runs=runs, decisions=decisions, campaigns=campaigns,
                current_packet_id=current["id"] if current else None, ready=ready,
                missing=completeness(current["document"]) if current else list(FIELDS) + ["Candidate protocol"],
                limitations="Plan readiness is not scientific validation. Fresh AI sessions may share models and sources; independence is not asserted.")

def save(study_id, actor, body):
    if set(body) - {"action", "request_id", "supersedes_id", "document", "reason"}: raise ValueError("Unsupported idea draft fields.")
    value = document(body.get("document")); raw = canonical(value).decode()
    return store.rpc("research_save_idea_packet", dict(p_study=study_id, p_actor=actor, p_id=identifier(body.get("request_id")),
        p_previous=identifier(body["supersedes_id"]) if body.get("supersedes_id") else None,
        p_document=value, p_raw=raw, p_sha256=digest(raw.encode()), p_reason=text(body.get("reason"), "Revision reason", 2000)))

def accept(study_id, actor, body):
    if set(body) - {"action", "request_id", "packet_id", "comprehension_run_id", "critic_run_id", "reason"}: raise ValueError("Unsupported readiness fields.")
    selected = packet(study_id, body.get("packet_id"))
    missing = completeness(selected["document"])
    if missing: raise ValueError("Plan remains incomplete: " + "; ".join(missing))
    return store.rpc("research_accept_idea_plan", dict(p_study=study_id, p_actor=actor, p_id=identifier(body.get("request_id")),
        p_packet=selected["id"], p_comprehension=identifier(body.get("comprehension_run_id")),
        p_critic=identifier(body.get("critic_run_id")), p_reason=text(body.get("reason"), "Readiness rationale", 2000)))

def prepare_campaign(study, actor, body, retained=None):
    allowed = {"action", "request_id", "packet_id", "title", "stage", "claim_type", "experiment_type", "planned_units"}
    if set(body) - allowed: raise ValueError("Unsupported idea campaign fields.")
    selected = packet(study["id"], body.get("packet_id")); value = selected["document"]
    if completeness(value): raise ValueError("Complete and review the current plan before freezing an experimental campaign.")
    if retained:
        from claim_design import validate_binding
        validate_binding(value, retained)
        if any(body.get(key) != value['protocol'].get(field) for key, field in (('stage', 'stage'), ('experiment_type', 'experiment_type'))):
            raise ValueError('Campaign stage and method must match the reviewed paper-claim protocol.')
    request_id = identifier(body.get("request_id")); namespace = uuid.UUID(request_id)
    def uid(name): return str(uuid.uuid5(namespace, name))
    protocol = dict(value["protocol"], idea_packet_id=selected["id"], idea_packet_sha256=selected["sha256"])
    executable = network_spec.specs(protocol)
    source = validate_record("sources", dict(id=uid("source"), citation="Unpublished research proposal: " + study["title"],
        source_url=None, retrieved_url=None, kind="unpublished-proposal", role="Original hypothesis and candidate experimental plan",
        retrieved_version="Idea packet " + str(selected["version"]), sha256=selected["sha256"], byte_count=len(selected["raw_document"].encode()),
        access_status="retrieved", review_status="retrieved-unreviewed", review_definition="Original proposal bytes retained; no literature review inferred.",
        examined="Planning comprehension/critic review recorded separately; no empirical findings.", unexamined="Related literature, domain validity and empirical claims require substantive review.",
        retrieval_attempts=[dict(kind="owner-maintained proposal", transformation="Canonical UTF-8 idea packet; not a published paper")],
        reviewer=dict(identity="Idea packet import", type="AI", independence="Deterministic import; no substantive source review asserted"),
        findings="A proposed hypothesis, not an established result.", limitations="Novelty, accuracy and practical effectiveness are unestablished."))
    claim = validate_record("claims", dict(id=uid("claim"), source_id=source["id"], location="Idea packet hypothesis",
        description=value["hypothesis"], claim_type=body.get("claim_type"), scope=dict(origin="unpublished-hypothesis", conditions=value["conditions"], packet_id=selected["id"]),
        metrics=protocol["metrics"], priority=3))
    if retained:
        source = store.rows('sources', {'study_id': 'eq.' + study['id'], 'id': 'eq.' + retained['source_id'], 'limit': 1})[0]
        claim = store.rows('claims', {'study_id': 'eq.' + study['id'], 'id': 'eq.' + retained['claim_id'], 'limit': 1})[0]
        from workflow import FIELDS
        source = {key: source[key] for key in ('id', *FIELDS['sources'])}
        claim = {key: claim[key] for key in ('id', *FIELDS['claims'])}
    frozen = protocol_record(protocol, uid("protocol"))
    campaign = validate_record("campaigns", dict(id=uid("campaign"), title=text(body.get("title"), "Campaign title", 300),
        protocol_id=frozen["id"], stage=body.get("stage"), experiment_type=body.get("experiment_type"),
        adapter="network-execution-v1" if executable else "domain-operator", planned_units=body.get("planned_units"), followup_of=None,
        coverage_limits=value["limitations"] + " Preparation collects no measurements; scientific findings require recorded execution and claim review."))
    if campaign["stage"] not in ("pilot", "main") or campaign["experiment_type"] not in ("new-measurement", "independent-check", "broader-validation") or campaign["planned_units"] < 1:
        raise ValueError("Declare a pilot/main experiment with a positive number of planned units and an applicable experimental method.")
    if campaign["planned_units"] != protocol["planned_units"]:
        raise ValueError("Campaign units differ from the reviewed candidate protocol; revise and review the plan first.")
    check = validate_record("claim_checks", dict(id=uid("check"), claim_id=claim["id"], campaign_id=campaign["id"],
        method="Preregistered networking execution" if executable else "Preregistered domain experiment; operator execution required", applicability="applicable", rationale=value["falsification"]))
    artifact = store.artifact(study["id"], actor, selected["raw_document"].encode(), uid("artifact"), "application/json",
        dict(origin="paper-claim-design" if retained else "unpublished-proposal", packet_id=selected["id"], transformation="None from the retained canonical packet bytes; not empirical data"))
    entries = [dict(kind=kind, record=row) for kind, row in (("artifacts", artifact), ("sources", source), ("claims", claim), ("protocols", frozen), ("campaigns", campaign), ("claim_checks", check))]
    for index, configuration in enumerate(protocol["configurations"]):
        entries.append(dict(kind="configurations", record=validate_record("configurations", dict(id=uid("configuration:" + str(index)),
            identity=configuration["identity"], details=configuration["details"], input_versions=protocol["input_versions"], requested_resources=protocol["resource_limits"]))))
    entries.append(dict(kind="gaps", record=validate_record("gaps", dict(id=uid("gap"), claim_id=claim["id"], assessment_id=None, supersedes_id=None,
        status="planned", reason="This proposed campaign has no completed experimental check; original findings remain separate.", next_check=campaign["title"],
        required_inputs=protocol["input_versions"], dependencies=[dict(description="Registered networking executor and recorded measurements" if executable else "Domain implementation, operator and recorded measurements", status="required")],
        feasibility="Executable configuration prepared; queue activation and actual recorded outcomes still required." if executable else "Domain-specific execution is required; no automatic experiment is implied by preparation.", estimated_cost_usd=protocol["resource_limits"].get("estimated_cost_usd"),
        cost_basis=protocol["resource_limits"].get("cost_basis", "Missing charges are unknown, not zero."), decision_rule=value["falsification"],
        stopping_rule=protocol["stopping_rule"], priority=3, campaign_id=campaign["id"]))))
    request = {key: body[key] for key in allowed if key in body and key != "action"}
    result = store.rpc("research_prepare_idea_campaign", dict(p_study=study["id"], p_actor=actor, p_id=request_id, p_packet=selected["id"],
        p_request=request, p_records=entries, p_campaign=campaign["id"], p_protocol=frozen["id"]))
    if executable:
        requests=[dict(request_id=uid('network-job:'+str(index)),campaign_id=result['campaign_id'],execution_mode='automatic',priority=3,dependencies=[],
            exclusive_resources=protocol['execution'].get('exclusive_resources',[]),raw_input=canonical(spec).decode(),
            rationale='Execute the frozen networking specification and retain actual measurements; no automatic claim finding.',followup_of=None) for index,spec in enumerate(executable)]
        return dict(link=result,queue_request=requests[0],queue_requests=requests,execution=dict(version=network_spec.VERSION,runner=protocol['execution']['runner'],jobs=len(requests)))
    return dict(link=result, queue_request=dict(request_id=uid("job"), campaign_id=result["campaign_id"], execution_mode="manual", priority=3,
        dependencies=[], exclusive_resources=[], raw_input=None, rationale="Prepared experiment; requires domain operator and retained original measurements.", followup_of=None))

def resume_campaign(study, actor, body, retained=None):
    if set(body) != {"action", "prepared_id"}: raise ValueError("Resume uses the saved campaign identity only.")
    rows = store.rows("idea_campaign_links", {"study_id": "eq." + study["id"], "id": "eq." + identifier(body["prepared_id"]), "limit": 1})
    if not rows: raise ValueError("Prepared campaign is unavailable in this study.")
    return prepare_campaign(study, actor, dict(rows[0]["request"], action="prepare-campaign"), retained=retained)
