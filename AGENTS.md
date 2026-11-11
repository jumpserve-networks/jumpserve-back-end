# Repository Guidance

## Tooling

- Always use Context7 when work requires code generation, setup or configuration
  steps, or library, API, CLI, or cloud-service documentation. Resolve the relevant
  Context7 library identifier before querying its documentation unless an exact
  identifier is already available.
- Prefer primary and official sources for technical claims.

## Research assessment autonomy

The goal of JumpServe research assessments is **100% unattended autonomy** after
the user submits the paper or idea and confirms the assessment scope. The system
should plan, implement, validate and execute experiments, analyze measurements,
and diagnose, repair and resume recoverable failures without routine user input.
Installing dependencies, writing instrumentation and repairing experiment
packages are system responsibilities. Run claims in parallel where appropriate,
share compatible acquisitions, and expose repair and execution progress in
Experiment Queue. Persist prompts, responses, actions, failures, repairs and
measurement provenance as versioned Supabase records.

Continue useful work within approved spending, elapsed-time and authorization
limits, honoring cancellation. Unattended operation does not guarantee conclusive
findings: retain honest inconclusive or untested outcomes when evidence or
external prerequisites remain insufficient. Never fabricate data, weaken frozen
scientific criteria, reset limits or blindly replay uncertain calls to force
completion. Record the exact remaining blocker and retained evidence. This goal
does not authorize merging or deploying changes without an explicit user request.

## Named platform supervisor

The user explicitly authorized the **Platform Development Supervisor OpenClaw**
to merge and deploy qualified changes to the three JumpServe repositories after
initial activation. That named service uses its immutable Supabase authorization,
fixed checks, protected exact GitHub revisions, scoped AWS release roles, task
limits and owner controls without per-release human confirmation. Preserve those
boundaries and recorded production verification. This standing authority does
not apply to unrelated interactive editing sessions. Activation order and scope
are in `jumpserve-infra/docs/platform-supervisor-v1.md`.

The owner's latest standing instruction grants this named supervisor full
operational authority to enable unattended reproduction of academic-paper
experiments, without further questions or permission requests. This includes
worker skills/tools, dependencies, instrumentation, experiment environments,
JumpServe infrastructure and service roles, Supabase migrations, worker and
supervisor repairs, qualification, merges, production deployment, verification
and rollback. Routine missing capabilities are system implementation work, not
missing owner authorization. Implement and qualify required adapters rather than
asking the owner to install tools or authorize another release. Keep cancellation,
private data, original evidence, frozen scientific criteria and uncertain-action
reconciliation intact. The mandate does not itself prove that a capability is
installed or a release succeeded. See
`jumpserve-infra/docs/platform-supervisor-standing-authority-v1.md`.

## Project purpose

Maintain the networking benchmark, research-assessment, real-world execution and
platform-supervisor services. Persist configurations, runs and measurements in
Supabase, preserve access controls and use synthetic inputs for local tests.
Historical experiment programs and datasets have been removed from this working
tree at the owner request. Deployed resources and production records are managed
separately through explicitly authorized operational actions.
