# Original native recovery diagnostics — candidate qualification

## Scope

Adds only `platform_supervisor/capability_original_native_recovery_diagnostics.py`
and a new regression test module. No admission, authority, credential, recovery,
ledger, status, deadline, cancellation or release kernel changes are proposed.
The literal pack declares revision 1. The installed controller, not a sandbox
script, invokes `collect(context)` after verifying the installed module hash.

The collector is pinned to assessment `fe761052-6e57-4d47-a3de-163c6cad905b`,
assessment task `2277f9be-ce58-5e9b-b383-b282c7913e45`, incident
`115f6e06-6bc4-47fe-848c-a07b042f9cff`, dispatch
`6bf4dac3-01d2-56e1-a273-bea290fe94d1`, call
`e1dd7f5c-ab3b-44e1-a8e7-0129968f2f7a`, number 79, and request SHA-256
`3f91b5124edb8abcb6cdc1f6e1f3bd88c1ddfe2cf4c5cc710fbc3c6f96e0022f`.
Cross-incident observations cannot satisfy its evidence prerequisite.

## Local checks

- The supplied frozen `assets/platform/supervisor-runtime-gate.py` accepted only
  the new pack and new tests against a predecessor copy without those additions.
  This is a local scope check, not an installed-predecessor release receipt.
- 33 focused capability/self-runtime/diagnostic regressions passed.
- All 138 supervisor tests passed using explicit test-module loading and the
  supplied frozen reference gate. The runner patched only `self_runtime.ROOT`
  in memory to locate that unchanged supplied gate; no source gate was edited.
- Ordinary initial execution lacked the externally supplied gate; the source
  reference resolved that local prerequisite. `unittest.discover` also requires
  an importable tests directory, so the full run loaded existing modules by name.
- Tests cover matching originals, missing sidecar/response versus unavailable
  evidence, request/call/dispatch/hash conflicts, incomplete terminal stream,
  source fingerprints, scan uncertainty, collector failure propagation,
  redaction and bounds, and no network/filesystem access or packet mutation.

## Private prerequisite and release

The supplied assessment packet contains the requested unknown call but no
`native_recovery_diagnostics` original. The candidate therefore returns
`private-spool-observation-not-in-trusted-packet`, with spool, completion,
response, source fingerprint and scan evidence unavailable. This does not assert
that originals are absent, nor use the unrelated retained-provider report.

If diagnostics are needed beyond that explicit prerequisite, the existing
private controller must collect the exact incident's original spool observation,
completion/response originals, installed recovery source and last scan diagnostic
into indexed hash-verified evidence. The optional original observation contract
is documented in the pack README. Its presence is not claimed today. Private
exception/refusal details are retained in the original; the report emits a hash
and original artifact reference, not possibly secret-bearing log text.

Publish only through the existing `supervisor-runtime-release.yml` self-update
route, with controller fixed checks, predecessor-frozen qualification, exact
revision binding, protected CI and installed module hash/source verification.
There are no publication/cloud/database credentials in this workspace and no
external change was attempted. A proposal, fixture or installed pack is not
reconciliation, assessment resumption, replacement admission or scientific
completion.
