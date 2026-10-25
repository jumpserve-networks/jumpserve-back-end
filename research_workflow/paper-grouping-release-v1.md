# Paper grouping release

The `paper-review-selection-v1` response/storage envelope organizes retained
source-linked statements for owner review into central claims and testable
findings. The original statements, IDs, units and locations remain immutable.
Only explicitly selected IDs become experiment tasks after owner confirmation.
Earlier unconfirmed inventories can be organized without re-uploading the PDF
or resetting the original spending authorization and deadline.

The active controller policy is `paper-review-selection-policy-v2`; the original
v1 policy is retained for historical request reproducibility. Policy hashes and
the complete policy/context are saved in every numbered model request alongside
the existing published base prompt identity. The storage envelope stays v1 so
clients and the private reviewed RPC remain compatible. Policy v2 preserves
epistemic qualifiers, distinguishes observed quantities from targets/derived
predictions, and asks for semantic deduplication under matched conditions.
These remain AI suggestions, not scientific verdicts or complete paper review.

The release requires the private migration
`202610240001_paper_review_selection.sql`, exact matching API/controller/model
worker sources, passing isolated database guards and bounded actual-model
release checks. Source/schema mismatches must not be deployed. The base published
autonomy prompt is unchanged. Unrelated authentication and frontend work is not
part of this backend release.
