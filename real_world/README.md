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

The authenticated catalog uses `DescribeRegions(AllRegions=True)` and live zone
and instance-type offerings. Each machine has independent Region, zone ID, and
non-burstable x86 instance-type selection. AZ IDs identify the physical zone
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

## Durable lifecycle and evidence

The infrastructure adds `/real-world/*` to the existing benchmark API. The API
checks the Supabase access token against `/auth/v1/user` and requires Google
authentication. Status, cancellation, listing, and signed result downloads are
owner-scoped. The browser never receives AWS credentials. Request IDs make launch
retries idempotent; the workflow name is derived from the owner and request ID.

Jobs/configurations live in a separate DynamoDB table with owner/history and
active/deadline indexes. Raw per-machine JSON lives in a private, versioned S3
bucket. Both stores are retained on stack deletion. Reports contain iperf JSON,
per-second `ss` samples, queue counters, routing preflights, kernel/software
versions, load, and actual start timestamps. Partial artifacts remain available
on failures. Receiver results use Mbit/s, decimal MB, and seconds. These are
duration-based transfers; they are not emulated file-completion-time samples.

Step Functions advances the resumable controller through provisioning, bootstrap,
configuration, connectivity checks, the common start barrier, measurement, and
cleanup. DynamoDB leases prevent simultaneous workflow/reaper mutation.
Resources are tagged at creation with project, job ID, and expiry; discovery
by tags recovers from interrupted calls before a resource ID was persisted.
Instances are idempotently launched and have encrypted, delete-on-termination
root volumes, IMDSv2, and no SSH ingress. Their only role is SSM management.
Short-lived, object-specific signed PUT URLs upload results.

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
check the retained stores, authenticated API configuration, workflow, scheduled
reaper, and resource-tag restrictions. Live validation must verify successful
transfer, cancellation, and removal of every tagged EC2/network resource.

After deploying the infrastructure, `python3 -B real_world/smoke_test.py` previews
a bounded three-machine, ten-second test using the current AWS CLI login.
Add `--apply` to run it, or `--apply --cancel` to verify cancellation during
provisioning. Select additional Regions with `--bottleneck-region` and repeat
`--receiver-region` for multiple receivers. The command waits for teardown,
checks stored results and shared-queue traffic, and requests cancellation if
interrupted. It prints no credentials and uses an operator-only test owner;
these validation runs are not research repetitions.

Primary references: [WireGuard](https://www.wireguard.com/quickstart/),
[iperf3](https://software.es.net/iperf/invoking.html),
[AWS AZ IDs](https://docs.aws.amazon.com/global-infrastructure/latest/regions/az-ids.html),
[Ubuntu AMIs](https://documentation.ubuntu.com/aws/aws-how-to/instances/find-ubuntu-images/).
