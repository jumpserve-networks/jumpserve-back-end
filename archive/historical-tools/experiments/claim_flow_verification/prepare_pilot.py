"""Prepare the fixed operational pilot packet locally; never launch or publish."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT.parent / 'jumpserve-infra'
sys.path.insert(0, str(ROOT / 'research_workflow'))
import workflow
import ideas
import network_spec
import claim_design


def main():
    output = INFRA / '.test-artifacts/claim-flow/v1/pilot-inputs'
    output.mkdir(parents=True, exist_ok=False)
    source = json.loads((INFRA / '.test-artifacts/claim-design/propagation-before-verification.json').read_text())
    claim = next(c for c in source['records']['claims'] if c['id'] == '40b409cc-3aec-5fb9-a47e-b8d7fdecd072')
    paper = next(s for s in source['records']['sources'] if s['id'] == claim['source_id'])
    config = dict(num_clients=2, script='netem_nines.py', client_delays_ms=[0, 0], client_ccas=['bbr', 'bbr'],
                  client_file_sizes_mbytes=[1, 1], client_start_delays_ms=[0, 0], loss_pct=0,
                  bottleneck_all_client_rate_mbit=10, bottleneck_buffer_kbytes=64,
                  snapshot_interval_ms=100, snapshot_metrics_source='kernel')
    limits = dict(wall_seconds=600, max_instances=1, max_output_bytes=1000000, max_network_bytes=10000000)
    question = 'Can one controlled emulated two-flow invocation retain two valid client completion times and their exact configuration through the production pipeline?'
    limitation = ('Operational acquisition-feasibility pilot for the retained paper claim. A single zero-additional-delay configuration does not recreate Figure 4, its RTT grid, ACK-only proxy or sensitivity statistic, and cannot resolve the BBRv1 delay-sensitivity claim. No new scientific assessment or generalization is authorized.')
    protocol = dict(
        questions=[question], hypotheses=claim['description'],
        metrics=[dict(name='flow_completion_time_ms', units='ms', planned_observations=2, lower_bound=0)],
        configurations=[dict(identity='operational-netem-two-flow-v1', details=dict(test_config=config))],
        input_versions=[dict(identity='Original selected NINeS paper bytes; recorded partial-review metadata', sha256=paper['sha256']),
                        dict(identity='Requested netem_nines.py source in backend revision 4266de647fe4f61658d0ce53a5d976b81507648b', sha256=hashlib.sha256((ROOT/'netem_nines.py').read_bytes()).hexdigest()),
                        dict(identity='Requested netem_cubic_benchmark_nines.py supporting implementation', sha256=hashlib.sha256((ROOT/'netem_cubic_benchmark_nines.py').read_bytes()).hexdigest())],
        dates_or_epochs='2026-10-18 UTC production operational verification; record actual start/end times and observed deployed versions.',
        algorithms=['Linux TCP bbr selected for both clients, netem_nines.py two-flow topology and existing emulated runner; actual kernel/BBR version must be observed, not inferred from the label.',
                    'Producer maps recorded per-client emulated_runs.flow_completion_time_ms into the declared ms observation contract.'],
        seeds=dict(applicable=False, rationale='No randomized treatment assignment, loss or sampling algorithm; OS scheduling remains variable and no bitwise timing reproducibility is claimed.'),
        controls=['Exactly one invocation and one t3.medium; two 1000000-byte application payloads with configured simultaneous starts.',
                  'Zero added netem delay in either direction and zero configured packet loss; underlying RTT is not assumed to be zero.',
                  'Both flows use the same BBR selection and share 10 Mbit/s configured bottleneck and 64 kB configured buffer.',
                  'Compare saved/frozen configuration hashes and exact client identifiers; do not infer independent replications from the two flows.'],
        sensitivity=dict(applicable=False, rationale='This fixed operational acquisition check estimates no treatment effect; RTT grids, ACK equalization, larger files and repetitions require a separate reviewed protocol.'),
        replication_unit='One two-flow emulated invocation on one VM; client-1 and client-2 are dependent observations within it.',
        sample_size_rationale='One invocation and two client completion records exercise the requested production path. No scientific precision or paper-level comparison is claimed.',
        exclusions='No dropped runs, replacement VMs or retries of uncertain launches. Retain failures, partial output and all observation statuses.',
        missing_data='Missing, invalid and ambiguous records retain null and an explicit reason; recorded zero is valid. Do not impute or silently discard a client.',
        statistics=claim_design.POLICY['statistics'],
        dependence='Flows share one bottleneck and host. Samples and invocations are not assumed independent; no confidence interval or hypothesis test.',
        resource_limits=dict(max_observations=10, max_input_bytes=256000, wall_seconds=600, estimated_cost_usd=None,
                             cost_basis='One t3.medium for at most 600 seconds plus observed termination grace. Charges and bootstrap/control traffic remain unallocated; missing cost is not zero. Built-in runner does not enforce its declared network-byte threshold.'),
        stopping_rule='Stop on identity/configuration/unit mismatch, invalid model output or adverse review. At most one VM/invocation, 600 seconds plus 180 seconds cleanup observation. Preserve failure and terminate only the task-owned VM if normal cleanup fails.',
        prior_exposure='Primary Codex AI implementer inspected existing implementation, exposed development evaluations and first production clarification. This is an operational follow-up, not held-out model evaluation or independent scientific review.',
        coverage_limits=limitation, stage='pilot', experiment_type='new-measurement', planned_units=1,
        data_origin='new-empirical', decision_policy=claim_design.POLICY_IDENTITY,
        execution=dict(version=network_spec.VERSION, runner='emulated-netem-v1', replications=1, limits=limits,
                       exclusive_resources=['verification:claim-flow-v1']))
    protocol = network_spec.bind_inputs(protocol)
    packet = dict(problem='Close the production planning/storage/execution verification gap before designing a full paper replication.',
                  question=question, hypothesis=claim['description'],
                  conditions='One actual EC2 t3.medium running Linux network emulation in us-east-1. Two BBR flows, one 1000000-byte application transfer per client; zero added delay/loss, 10 Mbit/s shared bottleneck, 64 kB buffer, simultaneous configured starts. Actual kernel, BBR implementation and AMI identities remain to be recorded.',
                  related_work=paper['citation'] + ' Source remains partial-review; this campaign does not perform a full-text review or claim novelty.',
                  alternatives='Full RTT-grid and ACK-delay comparisons, author-artifact reproduction and steady-state workloads are separate future designs. These small transfers cannot estimate the wider claim. Host scheduling and setup can affect observed completion times.',
                  design='A single fixed-configuration operational pilot with two client completion observations. No treatment contrast or sensitivity estimate is calculated; acquisition/identity/storage/cleanup pass criteria are stated in the verification protocol.',
                  metrics='flow_completion_time_ms in ms, two planned observations keyed client-1 and client-2, finite nonnegative values. Preserve missing/invalid/ambiguous status and recorded zero.',
                  controls='One invocation is the replication unit. The two clients share a host/bottleneck and are not independent. Preserve exact input/configuration hashes and check normalized records against original per-client measurements. No inferential interval.',
                  falsification=claim_design.POLICY['decision_rule'],
                  inputs='Selected original paper SHA256 ' + paper['sha256'] + '. Use the registered emulated runner and netem_nines.py, exact configuration and input hashes below. Record actual deployed software and raw measurement provenance; no author dataset or ACK-proxy implementation is claimed available.',
                  limitations=limitation, protocol=protocol)
    workflow.validate_protocol(protocol)
    ideas.document(packet)
    assert ideas.completeness(packet) == []
    claim_design.validate_binding(packet, dict(document=dict(claim=claim, source=paper)))
    documents = {'packet.json':packet, 'protocol.json':protocol, 'configuration.json':config}
    manifest = {}
    for name, value in documents.items():
        raw = (json.dumps(value, indent=2) + '\n').encode()
        (output/name).write_bytes(raw)
        manifest[name] = dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(dict(directory=str(output), packet_bytes=len(workflow.canonical(packet)), planned_invocations=1, planned_observations=2, launches=0)))


if __name__ == '__main__':
    main()
