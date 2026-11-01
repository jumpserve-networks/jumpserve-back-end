# Native repair completion

The live trial `20261031T230926512822Z` repaired `convert` and passed both
incident tests by provider call 7. Subsequent calls reread completion details.
It reached its 12-call qualification allowance without writing a candidate or
incident metadata. That failed outcome and all original calls remain unchanged.
This was a qualification limit, not a restored per-assessment spending cap.

The builder now supplies absent incident-binding metadata from its validated
server context. Existing invalid or conflicting plans remain unchanged and
receive correction feedback. A delivered `finish-repair.py` command stages the
unchanged bytes of an explicitly selected prospective candidate. It does not
choose scientific settings, execute a campaign or assert that validation passed.
The server still requires an actual source change, executed incident tests,
matching frozen package identities and fresh scientific reviews.

The delivery manifest records metadata origin and hashes. Generated metadata
and the helper do not count as implementation progress. Candidate publication
is atomic and does not overwrite a different candidate. The helper rejects
symlinks, hardlinks, outside paths, oversized files and conflicting metadata.
Instructions name unittest discovery explicitly, since running a test file
without `unittest.main()` may run no tests. Dense packets retain the executable
interface and incident binding, using exact context references for optional
excerpts when necessary. Original context and trusted instructions stay intact.

Local verification uses:

```sh
python3 -m unittest discover -s research_workflow/tests
python3 research_workflow/tests/check_repair_completion_retained.py --archive /path/to/linux-evidence.zip
```

The retained-checkpoint control verifies the original archive, all 12 terminal
streams, pre-repair baseline and repaired source/test hashes. It delivers the
metadata and uses scripted selection of the original prospective fixture, then
runs the actual server test runner and candidate compiler. It uses local Python
instead of Docker and fixture persistence. It makes zero new provider calls,
leaves the original archive unchanged and does not attribute scripted actions to
the live model. Its report and original validation artifacts are retained under
`.test-artifacts/repair-completion-v1/`.

The [versioned validation protocol](policies/repair-completion-validation-v1.json)
also defines a **new** live qualification with the same 12-call allowance:

```sh
python3 research_workflow/tests/check_native_repair_handoff_linux.py --completion-v2 --live --qualification-access-token /absolute/access-token.json
```

That run requires a separately isolated Linux environment, the pinned runtime
and a safe credential owner. Do not overlap qualification with production
workers or refresh a copied OAuth grant. Prefer an owner-only file containing
only an expiring access token supplied by the existing credential owner. This
mode cannot refresh or read an OAuth grant. An explicit independently owned auth
directory is also supported; there is no fallback to the old local grant. The new mode cannot resume or relabel
the old failed trial. It requires a successful native completion-command output,
unchanged prospective candidate bytes and actual Docker server validation.

The new isolated Linux trial completed the repair and candidate handoff in nine
of the allowed twelve inference calls. Its original harness report remains
failed because the evidence reader required the completion command to be the
entire exec string. The model successfully ran it after another checked script
using `&&`. A separate zero-call retained-evidence review verifies its paired
incident-bound JSON result, every original terminal stream, unchanged prospective
candidate bytes, source changes and actual Docker test execution. No model,
experiment, candidate or test was replayed or rewritten. This establishes the
observed software-completion path; production release verification is tracked
in `jumpserve-infra/docs/repair-completion-qualification-v1.json` and Platform
Updates. It does not establish scientific correctness for arbitrary papers.
Assessment budgets, deadlines, cancellation, uncertain-call fences and frozen
scientific criteria are unchanged.
