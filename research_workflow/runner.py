"""Registered bounded adapters; never executes submitted code or shell commands."""
from collections import Counter
from pathlib import Path
from decimal import Decimal, DecimalException, localcontext
import json
import os
import sys
import time
import uuid
from workflow import VERSION, canonical, digest, finite, identifier, now, validate_protocol

ADAPTERS = ("matched-numeric-v1",)

def parsed_decimal(token):
    # Reject lexical precision loss and underflow before they can become a zero.
    value = float(token)
    if not Decimal(token).is_finite() or not __import__('math').isfinite(value) or Decimal(str(value)) != Decimal(token):
        return {'invalid_numeric_lexeme': token}
    return value

def key(row):
    if not isinstance(row, dict): raise ValueError("Observation must be an object.")
    fields = ("observation_id", "configuration_identity", "metric")
    if any(not isinstance(row.get(k), str) or not 1 <= len(row[k]) <= 300 for k in fields):
        raise ValueError("Every observation needs exact observation, configuration and metric identities.")
    return tuple(row[k] for k in fields)

def execute(raw, protocol, campaign, request_id, clock=time.monotonic):
    """Returns a preserved terminal run, measurements and descriptive summaries.

    Input is the exact UTF-8 JSON bytes: {published: [...], observed: [...]}.
    Published rows additionally contain source_id, location and extraction.
    Output differences are Decimal strings, avoiding binary-float threshold drift.
    """
    started = now(); tick = clock(); request_id = identifier(request_id)
    configured_memory = os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '')
    run = dict(id=request_id, campaign_id=campaign["id"], configuration_id=None, status="failed", reason=None,
      started_at=started, ended_at=started, analysis_version=VERSION, input_sha256=digest(raw), output_sha256=None,
      requested_resources=protocol["document"].get("resource_limits", {}), actual_resources={"execution": "bounded arithmetic", "experiment_instances": 0, "hosting_configured_memory_mb": int(configured_memory) if configured_memory.isdigit() else None, "hosting_measured_peak_memory_bytes": None},
      usage=dict(input_bytes=len(raw), wall_seconds=None, measured_charges_usd=None, estimated_charges_usd=None, model_usage=None),
      provenance=dict(adapter=campaign["adapter"], data_origin=protocol['document'].get('data_origin','unclassified'), python_version=sys.version.split()[0], implementation_sha256=digest(Path(__file__).read_bytes()), protocol_id=protocol["id"], protocol_sha256=protocol["sha256"], independence="Shares supplied inputs; does not independently validate source measurements."))
    result = dict(run=run, published_values=[], measurements=[], summaries=[], comparisons=[])
    try:
        document = validate_protocol(protocol["document"])
        if digest(canonical(document)) != protocol["sha256"]: raise ValueError("Frozen protocol bytes changed.")
        if campaign["adapter"] not in ADAPTERS: raise ValueError("Adapter is not registered. Supply a domain implementation or documented external campaign; no invented test.")
        if campaign["experiment_type"] not in ("reanalysis", "independent-check"): raise ValueError("Numerical arithmetic cannot be labelled a new empirical or broader-validation campaign.")
        limits = document["resource_limits"]
        if len(raw) > limits["max_input_bytes"]: raise ValueError("Input byte budget exceeded.")
        if run["input_sha256"] not in [item["sha256"] for item in document["input_versions"]]: raise ValueError("Input SHA256 differs from the frozen protocol.")
        data = json.loads(raw, parse_float=parsed_decimal, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON input: " + value)))
        if not isinstance(data, dict) or set(data) != {"published", "observed"} or not all(isinstance(data[k], list) for k in data): raise ValueError("Input must contain published and observed arrays only.")
        if not data["published"]: raise ValueError("No published comparison observations were declared; empty data cannot validate a claim.")
        if len(data["published"]) != campaign["planned_units"]: raise ValueError("Published observation coverage differs from the campaign plan.")
        if len(data["published"]) + len(data["observed"]) > 2 * limits["max_observations"]: raise ValueError("Observation budget exceeded.")
        metrics = {row["name"]: row for row in document["metrics"]}
        configs = {row["identity"]: row for row in document["configurations"]}
        if len(configs) != len(document["configurations"]): raise ValueError("Duplicate configuration declarations.")
        indexed = {}
        for group in ("published", "observed"):
            indexed[group] = {}
            for row in data[group]:
                row_key = key(row)
                if row_key in indexed[group]: raise ValueError("Duplicate observation identity in " + group + ".")
                if row_key[1] not in configs or row_key[2] not in metrics: raise ValueError("Observation configuration or metric is not declared.")
                if row.get("units") != metrics[row_key[2]]["units"]: raise ValueError("Observation units do not match the protocol.")
                if row.get("status") not in ("recorded", "missing", "invalid", "ambiguous", "excluded"): raise ValueError("Unknown observation status.")
                if row["status"] != "recorded" and (row.get("value") is not None or not row.get("reason")): raise ValueError("Non-recorded observations need null values and explicit reasons.")
                indexed[group][row_key] = row
        if len(set(indexed["published"]) | set(indexed["observed"])) > limits["max_observations"]: raise ValueError("Unique observation budget exceeded.")
        for row_key, published in sorted(indexed["published"].items()):
            if clock() - tick > limits["wall_seconds"]: raise ValueError("Wall-time budget exceeded; no observations retried.")
            if published["status"] != "recorded": raise ValueError("This adapter needs recorded published values; it does not infer missing publication cells.")
            finite(published.get("value"), "published value")
            source_id = identifier(published.get("source_id"))
            if not published.get("location") or not published.get("extraction"): raise ValueError("Published value needs a source location and extraction provenance.")
            published_id = str(uuid.uuid5(uuid.UUID(request_id), "published:" + canonical(row_key).decode()))
            result["published_values"].append(dict(id=published_id, source_id=source_id, location=published["location"], observation_id=row_key[0], configuration_identity=row_key[1], metric=row_key[2], units=published["units"], value=published["value"], status="recorded", reason=None, extraction=published["extraction"]))
            observation = indexed["observed"].get(row_key)
            status, value, reason = (observation.get("status"), observation.get("value"), observation.get("reason")) if observation else ("missing", None, "Expected observation absent; not imputed.")
            if status == "recorded":
                if isinstance(value, dict) and 'invalid_numeric_lexeme' in value:
                    status, value, reason = 'invalid', None, 'Numeric token cannot be preserved by this adapter; original token remains in the raw input. Use a separately declared decimal adapter.'
            if status == "recorded":
                try: finite(value, "reproduced value")
                except ValueError as error: status, value, reason = "invalid", None, str(error)
            if status == "recorded":
                specification = metrics[row_key[2]]
                if ("lower_bound" in specification and value < specification["lower_bound"]) or ("upper_bound" in specification and value > specification["upper_bound"]):
                    status, value, reason = "invalid", None, "Observation outside the declared metric bounds; original raw value retained."
            with localcontext() as context:
                context.prec = 650 + len(str(value)) + len(str(published['value']))
                difference = Decimal(str(value)) - Decimal(str(published["value"])) if status == "recorded" else None
            tolerance = metrics[row_key[2]].get("tolerance_absolute")
            if tolerance is None: raise ValueError("Declare an absolute tolerance for every numerical metric.")
            agreement = difference.copy_abs() <= Decimal(str(tolerance)) if difference is not None else None
            comparison = dict(observation_id=row_key[0], configuration_identity=row_key[1], metric=row_key[2], units=published["units"], published=str(published["value"]), reproduced=str(value) if value is not None else None, difference=str(difference) if difference is not None else None, tolerance_absolute=str(tolerance), label=("reproduced" if agreement else "discrepant") if agreement is not None else "inconclusive", status=status, reason=reason)
            result["comparisons"].append(comparison)
            result["measurements"].append(dict(id=str(uuid.uuid5(uuid.UUID(request_id), "measurement:" + canonical(row_key).decode())), run_id=request_id, published_id=published_id, observation_id=row_key[0], configuration_identity=row_key[1], metric=row_key[2], units=published["units"], value=value, status=status, reason=reason, details=comparison))
        for row_key in sorted(set(indexed["observed"]) - set(indexed["published"])):
            row = indexed["observed"][row_key]
            result["measurements"].append(dict(id=str(uuid.uuid5(uuid.UUID(request_id), "measurement:" + canonical(row_key).decode())), run_id=request_id, published_id=None, observation_id=row_key[0], configuration_identity=row_key[1], metric=row_key[2], units=row["units"], value=None, status="excluded", reason="No matched published observation identity.", details={"original_status": row["status"]}))
        for name, metric in metrics.items():
            rows = [row for row in result["measurements"] if row["metric"] == name]
            counts = Counter(row["status"] for row in rows)
            differences = [Decimal(row["details"]["difference"]) for row in rows if row["status"] == "recorded"]
            unexpected = sum(row["published_id"] is None for row in rows)
            result["summaries"].append(dict(id=str(uuid.uuid5(uuid.UUID(request_id), "summary:" + name)), run_id=request_id, metric=name, units=metric["units"], planned=len(rows)-unexpected, unexpected=unexpected, **{s: counts[s] for s in ("recorded", "missing", "invalid", "ambiguous", "excluded")}, interval_kind="descriptive", uncertainty_method="Min/max are descriptive differences across supplied cells, not confidence intervals; no independence or population sampling established.", statistics=dict(min_difference=str(min(differences)) if differences else None, max_difference=str(max(differences)) if differences else None, reproduced=sum(row["details"].get("label") == "reproduced" for row in rows), discrepant=sum(row["details"].get("label") == "discrepant" for row in rows))))
        run["status"] = "partial" if any(row["status"] != "recorded" for row in result["measurements"]) else "complete"
        run["reason"] = "Missing, invalid, ambiguous or excluded observations retained; see coverage counts." if run["status"] == "partial" else None
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError, DecimalException) as error:
        run["status"], run["reason"] = "failed", str(error)
        # A partial computation is not silently promoted to a valid result.
        result.update(published_values=[], measurements=[], summaries=[], comparisons=[])
    run["ended_at"] = now(); run["usage"]["wall_seconds"] = max(0, clock() - tick)
    run["output_sha256"] = digest(canonical({k: v for k, v in result.items() if k != "run"}))
    return result
