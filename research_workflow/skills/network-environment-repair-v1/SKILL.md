---
name: network-environment-repair-v1
description: Diagnose and repair experiment tools and host capabilities without changing the frozen scientific method.
---

Use this skill when a networking acquisition lacks measurements because a program,
dependency, namespace, socket, qdisc or kernel capability failed. Tooling and
instrumentation are system work; do not ask the researcher to install programs.

1. Read the original command, stdout/stderr, return code, source hash and host
   versions. Follow the raw artifact references; a generic missing measurement
   message is not the diagnosis. Preserve failed observations and any valid data.
2. Inspect `runner_contracts` and the current environment repair contract. The
   fixed netem runner prepares a disposable EC2 host for the requested socket
   buffers, installs a hash-pinned seeded `tc` when necessary, and checks both
   endpoint sockets and actual kernel seed readback before measured traffic.
   `iperf3: socket buffer size not set correctly` requires sufficient send AND
   receive maxima on both endpoints. `tc: What is "seed"?` requires a toolchain
   with the seed parser AND a kernel that retains the requested seed.
3. In a native builder, use file/edit/exec tools for implementation and bounded
   software checks. Declare package dependencies through the existing pinned
   dependency lock and trusted provisioning path. Missing privileges in a
   network-free development sandbox are not proof that an experiment is
   impossible: real networking runs on the dedicated experiment host. Do not
   run privileged system configuration or the scientific campaign on the shared
   supervisor host. The trusted controller owns host provisioning and dedicated
   qualification; do not request cloud credentials in a model workspace.
4. If a fixed executor or its bootstrap needs a change, preserve a concrete
   operational incident for platform repair. The supervisor must reproduce the
   fault, implement the change, execute fixed checks and use the dedicated
   qualified runtime release path. Merely rewriting a prompt or recording a
   successful local fixture does not qualify an actual kernel/tool pair.
5. Resume with a versioned prospective acquisition and fresh scientific/cost
   reviews after the qualified implementation changes. Keep all old call/job
   identities, valid evidence, criteria, requested buffers and explicit seeds.
   Never remove a seed, shorten a transfer, alter a loss grid, lower a buffer,
   fabricate a measurement, reset a deadline or replay an uncertain invocation.

Sources: upstream iperf3 `docs/invoking.rst` and `src/iperf_tcp.c`; upstream
iproute2 v6.8.0 `tc/q_netem.c`; kernel.org's SHA256 release manifest. Installed
versions, source/binary hashes, commands, host changes and readbacks belong in
the original Supabase experiment artifacts. A successful environment check is
operational evidence, not a scientific conclusion.
