# Congestion Control Real World Tests

This module runs **one EC2 server, one EC2 bottleneck, and 1–16 EC2 receivers**
per test. It does not reuse the Linux network namespaces or `emulated_*` records.
`jumpserve-infra` packages this directory at a pinned Git revision for Lambda;
the controller sends `runtime.py` to fresh machines using Systems Manager.

## Workload and routing

The first workload is a simultaneous, duration-based TCP bulk transfer (10–600
seconds), one flow per receiver. iperf3 runs a separate server listener per
receiver and uses reverse mode, so the EC2 **server sends** the test data.
CUBIC, stock-kernel BBR, and Reno are supported. The requested CCA is explicitly
set and checked; unavailable CCAs fail the test instead of silently falling back.
Kernel and iperf versions are saved. This is not a BBRv2/BBRv3 implementation.

WireGuard forms a star: the server and each receiver have only the bottleneck
as their peer. Every test TCP connection binds an overlay address. All data,
control packets, and ACKs go through the bottleneck. No TCP service is exposed
on public/private underlay addresses. Security groups accept UDP 51820 only
from that machine's topology peers. SSM, package installation, and result uploads
use management connections outside the measured path.

The bottleneck forwards only server/receiver pairs. A single HTB class and byte
FIFO on its WireGuard egress impose the shared downstream rate (1–1,000 Mbit/s)
and queue limit (2–10,000 decimal kB). ACKs use a separate unshaped class.
The buffer is **one shared FIFO**, not one queue/rate allowance per receiver.
No artificial delay is applied. The overlay MTU is 1,380 bytes; tunnel offloads
are disabled and sender pacing uses `fq`. The Internet/AWS path and tunnel/CPU
capacity can impose additional bottlenecks. Configured rate is not a guarantee
of achieved throughput.

## Placement

The public catalog uses `DescribeRegions(AllRegions=True)` and live zone
and instance-type offerings. Each machine has independent Region and zone ID
selection and supports **t3.small**, **t3.medium**, and **t3.large** independently.
The default is t3.medium. The catalog lists the supported types offered in each
zone; the API verifies the requested type is available there. The provisioner
launches that exact type and rejects unsupported sizes before allocating resources.
EC2 launch permissions enforce the same three-type allowlist. AZ IDs identify the physical zone
consistently across accounts. AWS does not expose individual building selection.
Catalog entries explain Region/zone opt-in requirements and unsupported capacity.
Standard AZs and enabled Local Zones with supported offerings are usable.
Wavelength zones require a different carrier-gateway topology and are displayed
as unavailable. Other AWS partitions (China/GovCloud) require a separate account
and deployment. Opt-ins are never performed automatically.

The launcher validates placements again before accepting a test. Offering
availability does not guarantee capacity or quota; AWS allocation failures are
recorded and any partial resources are cleaned up. The runtime resolves the
regional Canonical Ubuntu 24.04 public SSM AMI parameter and records the image ID.

## Instance sizing

Small instances are a reasonable starting point for short, modest-rate tests,
but suitability must be verified against the requested workload. All three sizes
have two vCPUs. Memory is 2, 4, and 8 GiB for small, medium, and large respectively;
AWS lists baseline network bandwidths of 128, 256, and 512 Mbit/s and burst
bandwidth up to 5 Gbit/s. Burst bandwidth is best effort, not a sustained-rate
guarantee. The bottleneck also decrypts, forwards, shapes, and re-encrypts traffic
for every receiver, so CPU or underlay limits can dominate the intended queue.

Keep machine sizes identical across matched algorithm comparisons; instance type
already participates in the comparison key and is recorded in Supabase and raw
measurement provenance. T3 CPU credits and network I/O credits are separate.
The launch preserves the account’s existing CPU-credit mode; Unlimited mode can
incur surplus CPU charges, while Standard mode can throttle to baseline.

Sources: [AWS T3 specifications](https://aws.amazon.com/ec2/instance-types/t3/),
[network baselines](https://docs.aws.amazon.com/ec2/latest/instancetypes/gp.html),
[network burst behavior](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-network-bandwidth.html),
[CPU-credit modes](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-performance-instances-unlimited-mode.html).

## Durable lifecycle and evidence

The infrastructure adds `/real-world/*` to the existing benchmark API. The API
exposes the recorded `start_epoch` once the common transfer barrier is scheduled.
Together with `duration_seconds`, this supports schematic traffic animation in
the test map. Transfers occur inside the controller's `starting` phase while SSM
commands run; `running` can include waiting for measurement artifacts. This
schedule is not a live packet or throughput feed.

The API requires a verified Supabase Google session for launching, cancellation,
and the owner-scoped `/tests` history. Catalogs, test status, measurement reports,
and measurement downloads are public. Cancellation independently checks ownership. The browser never receives AWS credentials. Request IDs make launch
retries idempotent; the workflow name is derived from the owner and request ID.

Both congestion-control modules persist their data in **Supabase**. Emulated
measurements remain in the existing `emulated_*` tables. Real-world data uses:

- `real_world_jobs`: private configuration, owner, controller checkpoints,
  atomic cancellation, and fenced leases. Indexed owner history and deadlines.
- `real_world_runs`: public read-only job projections and indexed catalog.
- `real_world_reports`: public read-only normalized reports, receiver summaries,
  per-second throughput/TCP/queue traces, comparison keys, and provenance.
- `real_world_artifacts`: private manifests with SHA-256 digests, byte counts,
  object paths, and preserved legacy S3 version IDs.
- Private Storage bucket `real-world-results`: original per-machine JSON and
  immutable content-addressed archives. Upload capabilities are scoped to one
  staging object; completed evidence is archived outside that capability.

RLS is enabled on all tables. Only backend Lambdas can mutate these records,
using the existing Secrets Manager service key. No service key reaches EC2 or
browsers. Original DynamoDB/S3 stores remain as migration backups, without runtime
access. Supabase data is independent of CloudFormation stack deletion.

 Reports contain iperf JSON,
per-second `ss` samples, queue counters, routing preflights, kernel/software
versions, load, and actual start timestamps. Partial artifacts remain available
on failures. Receiver results use Mbit/s, decimal MB, and seconds. These are
duration-based transfers; they are not emulated file-completion-time samples.

Step Functions advances the resumable controller through provisioning, bootstrap,
configuration, connectivity checks, the common start barrier, measurement, and
cleanup. Postgres leases prevent simultaneous workflow/reaper mutation; expired
workers cannot overwrite records or release a successor’s lease. Cancellation
updates independently, so a concurrent checkpoint cannot erase it.
Resources are tagged at creation with project, job ID, and expiry; discovery
by tags recovers from interrupted calls before a resource ID was persisted.
Instances are idempotently launched and have encrypted, delete-on-termination
root volumes, IMDSv2, and no SSH ingress. Their only role is SSM management.
Object-specific signed PUT URLs upload results directly to Supabase Storage.
They expire after two hours and permit retrying the same object. Completed
reports use archived hashes, not mutable staging uploads.

Cancellation and errors converge on cleanup. `completed`, `failed`, and
`cancelled` are written **only after** all instances are terminated and the
per-test VPCs, subnets, gateways, route tables, and security groups are removed.
Cleanup errors remain visible and retry. A scheduled reaper handles expired
jobs even if the workflow never started or stopped running. Each job has a
45-minute deadline; the image schedules an independent 60-minute shutdown with
EC2 shutdown behavior set to terminate. AWS outages/IAM revocation can delay
cleanup; neither deadline is a billing guarantee. Tests incur EC2, IPv4, and
data-transfer charges as well as small control-plane/storage charges.

One job is one replication. Do not pool these results with emulations or infer
algorithm effects from unmatched paths, different instance types, or one trial.
Notes are retained as the hypothesis, not treated as evidence of a causal result.

## Verification

```bash
python3 -B -m unittest discover -s real_world/tests -v
```

These tests do not allocate AWS resources. Infrastructure tests additionally
check the retained legacy backups, Supabase credentials, API configuration, workflow, scheduled
reaper, and resource-tag restrictions. Live validation must verify successful
transfer, cancellation, and removal of every tagged EC2/network resource.

After deploying the infrastructure, `python3 -B real_world/smoke_test.py` previews
a bounded three-machine, ten-second test using the current AWS CLI login.
Add `--apply` to run it, or `--apply --cancel` to verify cancellation during
provisioning. Use `--instance-type t3.small` or `--instance-type t3.large` to
select another supported size for all machines (default: t3.medium). Select additional Regions with `--bottleneck-region` and repeat
`--receiver-region` for multiple receivers. The command waits for teardown,
checks stored results and shared-queue traffic, and requests cancellation if
interrupted. It prints no credentials and uses an operator-only test owner;
these validation runs are not research repetitions.

## Research reporting API

Install offline test dependencies with
`python3 -m pip install -r real_world/requirements-test.txt` before running tests.

- `GET /real-world/reports?cursor=...` returns newest-first shared test metadata,
  50 records per page. Postgres keyset pagination uses `(created_at, job_id)` so
  tests created in the same second are neither skipped nor duplicated.
- `GET /real-world/reports/{jobId}` returns normalized measurement traces,
  summary metrics, eligibility checks, provenance, and source SHA-256/version IDs.
  Add `?summary=1` to omit traces for bounded comparison requests.
- `GET /real-world/reports/{jobId}/artifacts` signs only expected machine JSON
  files for five minutes. It cannot sign arbitrary objects or internal commands.

Public GET routes omit owner IDs and internal command state. An optional verified
session sets `can_manage` for the owner; anonymous readers cannot manage tests.
POST routes reject missing or invalid sessions before privileged access. Both
`/tests/{jobId}/artifacts` and `/reports/{jobId}/artifacts` sign only expected
measurement files. Supabase Storage stays private; signed download URLs expire after five minutes.

`reports.py` defines `real-world-report-v1`. Receiver averages use bytes and actual
duration; combined throughput sums flow averages and is not a synchronized rate.
Jain fairness describes those averages, with one whole test as the replication
unit. Sender RTT is read in milliseconds from the exact data socket's full tuple;
control sockets and zero placeholders are excluded. Cwnd bytes are cwnd × MSS.
Only shared BFIFO `10:` contributes queue data. Estimated drain time is backlog
bytes × 8 / (rate Mbit/s × 1000), never RTT minus a presumed base delay.
Summary RTT/queue statistics exclude the post-test tail while traces retain it.

Comparison keys retain all non-CCA/non-notes configuration and per-machine
AMI/kernel/iperf/runtime provenance. Failed, incomplete, inconsistent, and operator
smoke tests receive explicit exclusions. Optional missing traces are warnings.
Malformed artifacts are partial evidence and missing values remain null. Reads
are capped at 32 MiB per object and 64 MiB total. Frontend comparisons preserve
separate configuration blocks, independent whole-test replication counts and
exploratory bootstrap intervals; see `jumpserve-front-end/docs/real-world-reports.md`.

Primary references: [WireGuard](https://www.wireguard.com/quickstart/),
[iperf3](https://software.es.net/iperf/invoking.html),
[AWS AZ IDs](https://docs.aws.amazon.com/global-infrastructure/latest/regions/az-ids.html),
[Ubuntu AMIs](https://documentation.ubuntu.com/aws/aws-how-to/instances/find-ubuntu-images/).

## Storage migration and verification

Apply `jumpserve-infra/database/202609200004_real_world_supabase.sql` before
updating the pinned runtime. It creates the tables, private bucket, backend-only
RPCs, and RLS policies. `npm run test:database:rls` in infra verifies actual SQL
lease/cancellation behavior and public/private permissions in a rolled-back
Postgres transaction. The reaper retries final report persistence independently
of resource cleanup; failed storage calls do not strand EC2 resources.

With operator AWS credentials, `python3 -B real_world/migrate_supabase.py` inventories
legacy jobs without writing. `--apply` imports only terminal jobs, never overwrites
existing job records, copies original bytes, preserves source version IDs, and
verifies hashes and analysis outputs. `--verify` rechecks the import without
writing. `--check-storage` checks direct signed upload, retry, and signed download
using one temporary object and removes only that object. None starts EC2.

For the initial cutover, briefly quiesce the legacy API, wait for in-flight API
requests to drain, and verify no legacy jobs remain active. Re-run the import,
deploy all three real-world Lambdas together, then restore API concurrency and
verify public catalog/detail/downloads and authenticated mutation rejection.
Do not switch stores while an old worker owns a running test. Keep legacy stores
until the import and deployed paths have been verified; this migration does not
delete them. Future deployments need no legacy import or API quiescence.
